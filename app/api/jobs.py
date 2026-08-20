"""任务端点：创建 / 快照 / SSE / 题目 / 产物下载 / 取消 / 删除。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.exceptions import (
    AppException,
    ErrorCode,
    JobAlreadyFinished,
    JobNotFound,
    JobNotReady,
    RenderFailed,
    UploadNotFound,
)
from app.core.storage import storage
from app.models.job import Job, JobStage, JobUpload
from app.models.question import Question
from app.models.upload import Upload
from app.models.user import AppUser
from app.orchestration.events import EventBus, subscribe, unsubscribe
from app.orchestration.stages import run_mock_pipeline
from app.orchestration.state_machine import JobStatus, Stage, is_terminal
from app.schemas.job import (
    Artifacts,
    JobAccepted,
    JobCreate,
    Plan,
    Warning,
)
from app.schemas.job import (
    Job as JobSchema,
)
from app.schemas.question import Question as QuestionSchema
from app.schemas.question import QuestionsResponse

router = APIRouter(tags=["Jobs"])


def _job_to_schema(job: Job, last_event_seq: int) -> JobSchema:
    plan = None
    if job.planned_total is not None:
        # 分布仅作占位，真实分布来自 job_stage 的 plan_ready 事件
        plan = Plan(total=job.planned_total, distribution={})

    artifacts = Artifacts(
        md_url=f"/api/v1/jobs/{job.id}/paper.md" if job.md_key else None,
        pdf_url=f"/api/v1/jobs/{job.id}/paper.pdf" if job.pdf_key else None,
    )
    if is_terminal(job.status) and job.md_key is None and job.pdf_key is None:
        artifacts = Artifacts(md_url=None, pdf_url=None)

    return JobSchema(
        job_id=job.id,
        status=JobStatus(job.status),
        stage=Stage(job.status) if job.status in {s.value for s in Stage} else None,
        duration_minutes=job.duration_minutes,
        need_explanation=job.need_explanation,
        enable_review=job.enable_review,
        plan=plan,
        progress=None,
        warnings=[Warning(**w) for w in (job.warnings or [])],
        artifacts=artifacts,
        last_event_seq=last_event_seq,
        error_code=job.error_code,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


async def _get_owned_job(db: AsyncSession, job_id: uuid.UUID, user: AppUser) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise JobNotFound()
    return job


@router.post("/jobs", response_model=JobAccepted, status_code=202)
async def create_job(
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobAccepted:
    # 校验 upload_ids 归属
    uploads: list[Upload] = []
    if payload.upload_ids:
        rows = (
            await db.scalars(
                select(Upload).where(
                    Upload.id.in_(payload.upload_ids), Upload.user_id == user.id
                )
            )
        ).all()
        found = {u.id for u in rows}
        missing = [str(i) for i in payload.upload_ids if i not in found]
        if missing:
            raise UploadNotFound()
        uploads = list(rows)

    # FR-3：至少一项输入
    text_uploads = [u for u in uploads if u.raw_text is not None]
    if not payload.upload_ids and not text_uploads:
        raise AppException(ErrorCode.INPUT_EMPTY, "无任何输入")

    # 共享需指定学校课程（若有共享项）
    if any(u.shareable for u in uploads) and (not payload.school_id or not payload.course_id):
        raise AppException(
            ErrorCode.SHARE_SCOPE_REQUIRED, "共享但未指定学校课程"
        )

    job = Job(
        user_id=user.id,
        school_id=payload.school_id,
        course_id=payload.course_id,
        status=JobStatus.pending.value,
        duration_minutes=payload.duration_minutes,
        need_explanation=payload.need_explanation,
        enable_review=payload.enable_review,
    )
    db.add(job)
    await db.flush()

    for u in uploads:
        db.add(JobUpload(job_id=job.id, upload_id=u.id))

    await db.commit()
    await db.refresh(job)

    # 异步模拟 pipeline
    background_tasks.add_task(run_mock_pipeline, job.id)

    return JobAccepted(job_id=job.id, status=JobStatus.pending, created_at=job.created_at)


@router.get("/jobs/{job_id}", response_model=JobSchema)
async def get_job(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobSchema:
    job = await _get_owned_job(db, job_id, user)
    last_seq = await db.scalar(
        select(func.coalesce(func.max(JobStage.seq), 0)).where(JobStage.job_id == job_id)
    )
    return _job_to_schema(job, last_seq or 0)


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    request: Request,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    await _get_owned_job(db, job_id, user)

    # 断线重连续传：从 Last-Event-ID 之后补发
    last_event_id = request.headers.get("Last-Event-ID")
    after_seq = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    queue = subscribe(job_id)

    async def event_generator():
        try:
            # 1. 先补发历史事件（> after_seq）
            rows = (
                await db.scalars(
                    select(JobStage)
                    .where(JobStage.job_id == job_id, JobStage.seq > after_seq)
                    .order_by(JobStage.seq)
                )
            ).all()
            for row in rows:
                yield _sse_frame(row.seq, row.event_type, row.payload)

            # 2. 再实时推送新事件
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _sse_frame(evt["seq"], evt["event"], evt["data"])
                except TimeoutError:
                    # 心跳注释行，保持连接
                    yield ": keep-alive\n\n"
        finally:
            unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_frame(seq: int, event: str, data: dict) -> str:
    return f"id: {seq}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/jobs/{job_id}/questions", response_model=QuestionsResponse)
async def list_questions(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QuestionsResponse:
    job = await _get_owned_job(db, job_id, user)
    rows = (
        await db.scalars(
            select(Question)
            .where(Question.job_id == job_id)
            .order_by(Question.seq)
        )
    ).all()

    questions = [
        QuestionSchema(
            seq=q.seq,
            question_type=q.question_type,
            stem=q.stem,
            options=q.options,
            answer=q.answer,
            explanation=q.explanation if job.need_explanation else None,
            sub_questions=q.sub_questions,
            sub_answers=q.sub_answers,
        )
        for q in rows
    ]
    return QuestionsResponse(
        job_id=job_id,
        need_explanation=job.need_explanation,
        total=len(questions),
        questions=questions,
    )


@router.get("/jobs/{job_id}/paper.md")
async def download_paper_md(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = await _get_owned_job(db, job_id, user)
    if job.md_key is None:
        raise JobNotReady()
    try:
        data = await storage.get(job.md_key)
    except Exception as exc:  # noqa: BLE001
        raise JobNotReady() from exc
    return Response(
        content=data,
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/jobs/{job_id}/paper.pdf")
async def download_paper_pdf(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = await _get_owned_job(db, job_id, user)
    if job.pdf_key is None:
        # partially_completed 且 md 存在但 PDF 失败 → RENDER_FAILED
        if job.md_key is not None:
            raise RenderFailed()
        raise JobNotReady()
    try:
        data = await storage.get(job.pdf_key)
    except Exception as exc:  # noqa: BLE001
        raise JobNotReady() from exc
    return Response(content=data, media_type="application/pdf")


@router.post("/jobs/{job_id}/cancel", status_code=204)
async def cancel_job(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = await _get_owned_job(db, job_id, user)
    if is_terminal(job.status):
        raise JobAlreadyFinished()

    job.status = JobStatus.cancelled.value
    job.finished_at = datetime.now(UTC)
    await db.commit()

    bus = EventBus(db)
    await bus.emit(job_id, "done", {"status": JobStatus.cancelled.value})
    await db.commit()
    return Response(status_code=204)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = await _get_owned_job(db, job_id, user)

    # 删除对象存储产物（jobs/{job_id}/**）
    for key in (job.md_key, job.pdf_key):
        if key:
            try:
                await storage.delete(key)
            except Exception:  # noqa: BLE001
                pass

    await db.delete(job)  # CASCADE 删除 job_stage / plan_item / question / job_upload 等
    await db.commit()
    return Response(status_code=204)

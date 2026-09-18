"""任务端点：列表 / 创建 / 快照 / SSE / 取消 / 删除。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.events import EventBus, subscribe, unsubscribe
from app.core.exceptions import (
    AppException,
    ErrorCode,
    JobAlreadyFinished,
    JobNotFound,
    SchoolNotFound,
    UploadNotFound,
)
from app.core.storage import storage
from app.core.task_registry import cancel as cancel_task
from app.core.task_registry import register
from app.models.catalog import Course, School
from app.models.job import Job, JobStage, JobUpload
from app.models.plan import PlanItem
from app.models.upload import Upload
from app.models.user import AppUser
from app.orchestration.stages import _run_pipeline_with_cancellation
from app.orchestration.state_machine import TERMINAL_STATUSES, JobStatus, Stage, is_terminal
from app.schemas.job import (
    Artifacts,
    JobAccepted,
    JobCreate,
    JobsResponse,
    Plan,
    Warning,
)
from app.schemas.job import (
    Job as JobSchema,
)

router = APIRouter(tags=["Jobs"])

logger = logging.getLogger(__name__)


async def _build_plan(db: AsyncSession, job: Job) -> Plan | None:
    """从 DB 聚合蓝图分布（agent 产物入库后才有数据）。"""
    dist_rows = await db.execute(
        select(PlanItem.question_type, func.count(PlanItem.id))
        .where(PlanItem.job_id == job.id, PlanItem.superseded_by.is_(None))
        .group_by(PlanItem.question_type)
    )
    distribution = {question_type: count for question_type, count in dist_rows.all()}
    if not distribution:
        return None
    return Plan(total=sum(distribution.values()), distribution=distribution)


async def _job_to_schema(
    db: AsyncSession, job: Job, last_event_seq: int, *, with_progress: bool = True
) -> JobSchema:
    plan: Plan | None = None
    if with_progress:
        plan = await _build_plan(db, job)
    if plan is None and job.planned_total is not None:
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
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobAccepted:
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

    text_uploads = [u for u in uploads if u.raw_text is not None]
    if not payload.upload_ids and not text_uploads:
        raise AppException(ErrorCode.INPUT_EMPTY, "无任何输入")

    if payload.school_id is not None or payload.course_id is not None:
        if payload.school_id is None or payload.course_id is None:
            raise AppException(
                ErrorCode.SHARE_SCOPE_REQUIRED, "school_id 与 course_id 必须同时提供"
            )
        if await db.get(School, payload.school_id) is None:
            raise SchoolNotFound()
        course = await db.get(Course, payload.course_id)
        if course is None or course.school_id != payload.school_id:
            raise AppException(
                ErrorCode.SHARE_SCOPE_REQUIRED, "课程不存在或不属于该学校"
            )

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
    )
    db.add(job)
    await db.flush()

    for u in uploads:
        db.add(JobUpload(job_id=job.id, upload_id=u.id))

    await db.commit()
    await db.refresh(job)

    # 使用 asyncio 任务启动 pipeline，以便后续可以取消
    register(job.id, _run_pipeline_with_cancellation(job.id))

    return JobAccepted(job_id=job.id, status=JobStatus.pending, created_at=job.created_at)


@router.get("/jobs", response_model=JobsResponse)
async def list_jobs(
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobsResponse:
    """当前用户的任务列表（最近 50 条，创建时间倒序）。"""
    rows = (
        await db.scalars(
            select(Job)
            .where(Job.user_id == user.id)
            .order_by(Job.created_at.desc())
            .limit(50)
        )
    ).all()

    seq_by_job: dict[uuid.UUID, int] = {}
    if rows:
        seq_rows = await db.execute(
            select(JobStage.job_id, func.max(JobStage.seq))
            .where(JobStage.job_id.in_([j.id for j in rows]))
            .group_by(JobStage.job_id)
        )
        seq_by_job = {job_id: seq or 0 for job_id, seq in seq_rows.all()}

    return JobsResponse(
        jobs=[
            await _job_to_schema(db, j, seq_by_job.get(j.id, 0), with_progress=False)
            for j in rows
        ]
    )


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
    return await _job_to_schema(db, job, last_seq or 0)


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    request: Request,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    # 鉴权、订阅与补发快照全部在流式开始前完成，随后立即释放数据库连接；
    # 流式阶段只依赖进程内事件队列，不再长期占用连接池。
    await _get_owned_job(db, job_id, user)

    last_event_id = request.headers.get("Last-Event-ID")
    after_seq = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    # 先订阅后取快照，事件最多重复不丢失；重复由 replayed_max 去重
    queue = subscribe(job_id)
    rows = (
        await db.scalars(
            select(JobStage)
            .where(JobStage.job_id == job_id, JobStage.seq > after_seq)
            .order_by(JobStage.seq)
        )
    ).all()
    replay = [(row.seq, row.event_type, row.payload) for row in rows]
    replayed_max = replay[-1][0] if replay else after_seq

    # 结束当前事务，把连接归还连接池（快照数据已取到内存，不再触库）
    await db.rollback()

    async def event_generator():
        try:
            for seq, event_type, payload in replay:
                yield _sse_frame(seq, event_type, payload)

            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if evt.get("__close__"):
                    break
                if evt.get("seq", 0) <= replayed_max:
                    # 补发快照已包含该事件（订阅与快照之间的窗口），去重
                    continue
                yield _sse_frame(evt["seq"], evt["event"], evt["data"])
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


@router.post("/jobs/{job_id}/cancel", status_code=204)
async def cancel_job(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _get_owned_job(db, job_id, user)

    # 条件更新：仅当仍处于非终态时落 cancelled，避免与 pipeline 的终态提交
    # 竞态时用过期快照把 completed/partially_completed 覆写成 cancelled
    result = await db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.not_in([s.value for s in TERMINAL_STATUSES]),
        )
        .values(status=JobStatus.cancelled.value, finished_at=datetime.now(UTC))
    )
    await db.commit()
    if result.rowcount == 0:
        raise JobAlreadyFinished()

    # 尝试取消正在运行的任务进程
    cancel_task(job_id)

    bus = EventBus(db)
    await bus.emit_and_close(job_id, "done", {"status": JobStatus.cancelled.value})
    await db.commit()
    return Response(status_code=204)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = await _get_owned_job(db, job_id, user)

    # 运行中先取消，避免删除后管线继续消耗 LLM 调用并写库
    cancel_task(job_id)

    # 按前缀清理产物、解析产物与 agent 工作区，不留孤儿对象
    try:
        await storage.delete_prefix(f"jobs/{job_id}/")
    except Exception:  # noqa: BLE001
        logger.warning("清理任务存储前缀失败（job=%s）", job_id)

    await db.delete(job)
    await db.commit()
    return Response(status_code=204)

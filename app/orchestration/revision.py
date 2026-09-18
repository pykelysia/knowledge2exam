"""修订编排：同一 agent 全工具续跑，按轮次留存会话记录。

每轮流程：快照当前试卷 → 落一行 paper_revision（running）→ 后台以修订模式
调用 run_exam_agent（携带划选锚点、反馈与全部历史轮次）→ 结束后比对试卷：
有修改则留存快照并置 done，否则置 failed。事件经 EventBus 推送
（revision_started / revision_done / revision_failed）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AgentHooks, run_exam_agent
from app.core.db import AsyncSessionLocal
from app.core.events import EventBus
from app.core.exceptions import ErrorCode, PaperNotReady
from app.core.storage import storage
from app.core.task_registry import register
from app.models.job import Job, JobUpload
from app.models.revision import PaperRevision
from app.orchestration.state_machine import JobStatus

logger = logging.getLogger(__name__)

REVISIONABLE_STATUSES = (JobStatus.completed.value, JobStatus.partially_completed.value)


def _paper_key(job_id: uuid.UUID) -> str:
    return f"jobs/{job_id}/output/paper.md"


async def read_paper(job_id: uuid.UUID) -> str:
    """读取当前试卷全文；不存在时抛 PaperNotReady。"""
    try:
        return (await storage.get(_paper_key(job_id))).decode("utf-8", errors="replace")
    except Exception as exc:
        raise PaperNotReady() from exc


async def start_revision(
    db: AsyncSession, job: Job, selection: dict, feedback: str
) -> PaperRevision:
    """创建修订轮并启动后台续跑任务。

    调用方保证：job 属于当前用户、处于可修订终态、没有正在运行的任务。
    """
    snapshot_before = await read_paper(job.id)

    round_no = 1 + (
        await db.scalar(
            select(func.count()).select_from(PaperRevision).where(PaperRevision.job_id == job.id)
        )
        or 0
    )
    revision = PaperRevision(
        job_id=job.id,
        round_no=round_no,
        status="running",
        selection=selection,
        feedback=feedback,
        snapshot_before=snapshot_before,
    )
    db.add(revision)
    await db.flush()

    bus = EventBus(db)
    await bus.emit(
        job.id,
        "revision_started",
        {"round_no": round_no, "feedback": feedback},
    )
    await db.commit()

    register(job.id, _run_revision_with_cancellation(revision.id))
    return revision


async def list_revisions(db: AsyncSession, job_id: uuid.UUID) -> list[PaperRevision]:
    """修订会话历史（新→旧）。"""
    rows = (
        await db.scalars(
            select(PaperRevision)
            .where(PaperRevision.job_id == job_id)
            .order_by(PaperRevision.round_no.desc())
        )
    ).all()
    return list(rows)


async def _run_revision_with_cancellation(revision_id: uuid.UUID) -> None:
    """包装修订轮：取消与意外失败的兜底收敛（不向上抛异常）。"""
    try:
        await _run_revision_round(revision_id)
    except asyncio.CancelledError:
        try:
            async with AsyncSessionLocal() as db:
                revision = await db.get(PaperRevision, revision_id)
                if revision is not None:
                    revision.status = "failed"
                    revision.error = "修订任务被取消"
                    await db.commit()
        except Exception:
            logger.exception("标记取消的修订轮失败（revision=%s）", revision_id)
        raise
    except Exception as exc:
        logger.exception("修订轮异常（revision=%s）", revision_id)
        try:
            async with AsyncSessionLocal() as db:
                revision = await db.get(PaperRevision, revision_id)
                if revision is not None:
                    revision.status = "failed"
                    revision.error = str(exc) or type(exc).__name__
                    bus = EventBus(db)
                    await bus.emit(
                        revision.job_id,
                        "revision_failed",
                        {"round_no": revision.round_no, "message": revision.error},
                    )
                    await db.commit()
        except Exception:
            logger.exception("修订失败兜底再次出错（revision=%s）", revision_id)


async def _run_revision_round(revision_id: uuid.UUID) -> None:
    """执行一轮修订：组装会话上下文 → 修订模式续跑 agent → 比对试卷收尾。"""
    async with AsyncSessionLocal() as db:
        revision = await db.get(PaperRevision, revision_id)
        if revision is None:
            return
        job = await db.get(Job, revision.job_id)
        if job is None:
            return

        # 会话记录：既往成功轮次（旧→新）注入提示词
        history_rows = (
            await db.scalars(
                select(PaperRevision)
                .where(PaperRevision.job_id == job.id, PaperRevision.status == "done")
                .order_by(PaperRevision.round_no)
            )
        ).all()
        history = [
            {
                "round_no": r.round_no,
                "selection": r.selection,
                "feedback": r.feedback,
                "summary": r.summary or "",
            }
            for r in history_rows
        ]
        upload_ids = list(
            (await db.scalars(select(JobUpload.upload_id).where(JobUpload.job_id == job.id))).all()
        )

        context = {
            "duration_minutes": job.duration_minutes,
            "need_explanation": job.need_explanation,
            "user_id": job.user_id,
            "school_id": job.school_id,
            "course_id": job.course_id,
            "upload_ids": upload_ids,
            "revision": {
                "selection": revision.selection,
                "feedback": revision.feedback,
                "history": history,
            },
        }

        async def on_warning(message: str) -> None:
            await EventBus(db).emit(
                job.id,
                "warning",
                {"code": ErrorCode.AGENT_WARNING, "message": message},
            )

        result = await run_exam_agent(job.id, context, AgentHooks(on_warning=on_warning))

        bus = EventBus(db)
        if result.render_status == "md_only":
            await bus.emit(
                job.id,
                "warning",
                {
                    "code": ErrorCode.RENDER_FAILED,
                    "message": result.render_error or "修订后 PDF 渲染失败，paper.md 已更新",
                },
            )

        try:
            paper_after = await read_paper(job.id)
        except PaperNotReady:
            paper_after = None

        if paper_after is not None and paper_after != revision.snapshot_before:
            revision.status = "done"
            revision.snapshot_after = paper_after
            revision.summary = (
                "修订完成" if result.completed_normally else "修订中断，已保留部分修改"
            )
            revision.applied_at = datetime.now(UTC)
            await bus.emit(
                job.id,
                "revision_done",
                {"round_no": revision.round_no, "render_status": result.render_status},
            )
        else:
            revision.status = "failed"
            revision.error = "agent 未对试卷产生任何修改"
            await bus.emit(
                job.id,
                "revision_failed",
                {"round_no": revision.round_no, "message": revision.error},
            )
        await db.commit()

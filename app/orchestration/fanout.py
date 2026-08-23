"""出题 fan-out 调度器（architecture.md 第 5 节）。"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.agents.question_writers import (
    generate_blank_all,
    generate_choice_all,
    generate_short_single,
)
from app.core.db import AsyncSessionLocal
from app.models.job import Job
from app.models.plan import PlanItem
from app.orchestration.events import EventBus
from app.retrieval.filters import FilterBuilder
from app.retrieval.vector_store import PgVectorStore


@dataclass
class FanoutResult:
    choice_questions: list[Any] = field(default_factory=list)
    blank_questions: list[Any] = field(default_factory=list)
    short_questions: list[Any] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


async def _run_with_limit(semaphore: asyncio.Semaphore, coro: Any) -> Any:
    async with semaphore:
        return await coro


async def run_fanout(
    db: Any,
    job_id: uuid.UUID,
    bus: EventBus,
    plan_items: list[PlanItem],
    need_explanation: bool,
    writer_model: str,
    knowledge_context: str,
    max_concurrent: int = 4,
) -> FanoutResult:
    """执行 fan-out 出题。"""

    semaphore = asyncio.Semaphore(max_concurrent)
    result = FanoutResult()

    choice_plans = [p for p in plan_items if p.question_type == "choice"]
    blank_plans = [p for p in plan_items if p.question_type == "blank"]
    short_plans = [p for p in plan_items if p.question_type == "short_answer"]

    tasks: list[tuple[str, Any]] = []

    if choice_plans:
        tasks.append(("choice", _run_with_limit(
            semaphore,
            generate_choice_all(
                db=db, job_id=job_id, plan_items=choice_plans,
                need_explanation=need_explanation, model=writer_model,
                knowledge_context=knowledge_context, bus=bus,
            ),
        )))

    if blank_plans:
        tasks.append(("blank", _run_with_limit(
            semaphore,
            generate_blank_all(
                db=db, job_id=job_id, plan_items=blank_plans,
                need_explanation=need_explanation, model=writer_model,
                knowledge_context=knowledge_context, bus=bus,
            ),
        )))

    for plan in short_plans:
        # 为简答题检索相关知识
        plan_knowledge = await _retrieve_knowledge_for_plan(db, job_id, plan)
        tasks.append(("short", _run_with_limit(
            semaphore,
            generate_short_single(
                db=db, job_id=job_id, plan_item=plan,
                need_explanation=need_explanation, model=writer_model,
                knowledge_context=plan_knowledge, bus=bus,
            ),
        )))

    task_outputs = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

    for (task_type, _), output in zip(tasks, task_outputs, strict=True):
        if isinstance(output, Exception):
            result.errors.append({"type": task_type, "error": str(output)})
            continue
        if task_type == "choice":
            result.choice_questions = list(output)
        elif task_type == "blank":
            result.blank_questions = list(output)
        elif task_type == "short":
            result.short_questions.append(output)

    return result


async def _retrieve_knowledge_for_plan(
    db: Any, job_id: uuid.UUID, plan: PlanItem
) -> str:
    """为单个 plan_item 检索相关知识库内容。"""

    job = await db.get(Job, job_id)
    if not job or not job.school_id or not job.course_id:
        return ""

    # 获取该任务的所有 upload_ids

    from app.models.job import JobUpload
    upload_ids = (
        await db.scalars(
            select(JobUpload.upload_id).where(JobUpload.job_id == job_id)
        )
    ).all()

    if not upload_ids:
        return ""

    vector_store = PgVectorStore(AsyncSessionLocal)

    # 检索叠加类内容（book / lecture / note）
    additive_types = ["book", "lecture", "note"]
    chunks: list[str] = []

    for source_type in additive_types:
        filter_expr = FilterBuilder.additive(
            user_id=job.user_id,
            school_id=job.school_id,
            course_id=job.course_id,
            source_type=source_type,
            upload_ids=list(upload_ids),
        )
        try:
            result = await vector_store.search(
                query=plan.exam_direction,
                filter_expr=filter_expr,
                top_k=3,
            )
            for c in result.chunks:
                chunks.append(c["text"])
        except Exception:
            continue

    return "\n\n".join(chunks)

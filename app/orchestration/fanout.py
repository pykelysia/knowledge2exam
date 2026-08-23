"""出题 fan-out 调度器（architecture.md 第 5 节）。"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agents.question_writers import (
    generate_blank_all,
    generate_choice_all,
    generate_short_single,
)
from app.models.plan import PlanItem
from app.orchestration.events import EventBus
from app.orchestration.state_machine import Stage


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
        tasks.append(("short", _run_with_limit(
            semaphore,
            generate_short_single(
                db=db, job_id=job_id, plan_item=plan,
                need_explanation=need_explanation, model=writer_model,
                knowledge_context=knowledge_context, bus=bus,
            ),
        )))

    task_outputs = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

    for (task_type, _), output in zip(tasks, task_outputs):
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

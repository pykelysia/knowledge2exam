"""整合层：把 agent 产物（蓝图）写入数据库。

试卷本体是工作区的 output/paper.md（对象存储直接交付），不再入库分题结构。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import ExamResult
from app.models.plan import PlanItem

logger = logging.getLogger(__name__)


async def persist_exam_result(
    db: AsyncSession,
    job_id: uuid.UUID,
    result: ExamResult,
) -> dict[str, int]:
    """把蓝图（plan_items）批量入库。

    - PlanItem 来自蓝图 todo（seq 唯一），放弃的题仍保留规划记录
      （knowledge_point 以「[已放弃]」标记）。
    """
    plan_items: list[PlanItem] = []
    for todo in sorted(result.plan_items, key=lambda t: t.seq):
        plan = PlanItem(
            job_id=job_id,
            seq=todo.seq,
            question_type=todo.question_type,
            knowledge_point=todo.knowledge_point,
            exam_direction=todo.exam_direction,
            difficulty=todo.difficulty,
        )
        db.add(plan)
        plan_items.append(plan)

    await db.flush()

    total = len(plan_items)
    abandoned = len(result.abandoned_seqs)
    logger.info(
        "agent 产物入库完成（job=%s）：plan=%d abandoned=%d",
        job_id,
        total,
        abandoned,
    )
    return {
        "total_plan_items": total,
        "abandoned": abandoned,
    }

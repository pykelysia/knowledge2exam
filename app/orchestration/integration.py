"""整合层：把 agent 产物（ExamResult）写入数据库。"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import ExamResult
from app.models.plan import PlanItem
from app.models.question import Question

logger = logging.getLogger(__name__)


async def persist_exam_result(
    db: AsyncSession,
    job_id: uuid.UUID,
    result: ExamResult,
) -> dict[str, int]:
    """把蓝图（plan_items）与题目（questions）批量入库。

    - PlanItem 来自蓝图 todo（seq 唯一），放弃的题仍保留规划记录；
    - Question 按 seq 与 PlanItem 关联（plan_item_id）。
    """
    plan_by_seq: dict[int, PlanItem] = {}
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
        plan_by_seq[todo.seq] = plan

    await db.flush()

    questions: list[Question] = []
    for q in sorted(result.questions, key=lambda t: t.seq):
        question = Question(
            job_id=job_id,
            plan_item_id=plan_by_seq[q.seq].id if q.seq in plan_by_seq else None,
            seq=q.seq,
            question_type=q.question_type,
            stem=q.stem,
            options=q.options,
            answer=q.answer,
            sub_questions=q.sub_questions,
            sub_answers=q.sub_answers,
            explanation=q.explanation,
            status="accepted",
            review_passed=True,
        )
        db.add(question)
        questions.append(question)

    await db.flush()

    total = len(questions)
    abandoned = len(result.abandoned_seqs)
    logger.info(
        "agent 产物入库完成（job=%s）：plan=%d question=%d abandoned=%d",
        job_id, len(plan_by_seq), total, abandoned,
    )
    return {
        "total_plan_items": len(plan_by_seq),
        "total_questions": total,
        "abandoned": abandoned,
    }

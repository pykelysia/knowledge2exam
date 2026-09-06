"""整合层：从文件读取 subagent 结果并写入数据库。"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.subagent_io import read_all_subagent_results
from app.config import settings
from app.models.job import Job
from app.models.plan import PlanItem
from app.models.question import Question

logger = logging.getLogger(__name__)


async def integrate_subagent_results(
    db: AsyncSession,
    job_id: uuid.UUID,
) -> dict[str, Any]:
    """读取所有 subagent 输出文件，整合到数据库。"""

    # 1. 读取所有 plan 文件（按 seq 排序）
    plan_files = read_all_subagent_results(job_id, "plan")
    plan_items = []
    for plan_data in plan_files:
        if plan_data.get("status") == "success":
            for item in plan_data.get("plan_items", []):
                plan = PlanItem(
                    job_id=job_id,
                    seq=item["seq"],
                    question_type=item["question_type"],
                    knowledge_point=item.get("knowledge_point", ""),
                    exam_direction=item.get("exam_direction", ""),
                    difficulty=item.get("difficulty", "medium"),
                    reference_source=item.get("reference_source"),
                )
                db.add(plan)
                plan_items.append(plan)

    await db.flush()

    # 2. 读取所有 question 文件
    question_files = []
    subagent_dir = Path(settings.storage_dir) / "jobs" / str(job_id) / "subagent"
    if subagent_dir.exists():
        question_files = sorted(subagent_dir.glob("question_*.json"))

    questions = []
    for q_file in question_files:
        try:
            q_data = json.loads(q_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if q_data.get("status") != "success":
            continue

        q = q_data.get("question", {})
        question = Question(
            job_id=job_id,
            seq=q["seq"],
            question_type=q["question_type"],
            stem=q.get("stem", ""),
            options=q.get("options"),
            answer=q.get("answer", ""),
            sub_questions=q.get("sub_questions"),
            sub_answers=q.get("sub_answers"),
            explanation=q.get("explanation"),
            status=q.get("status", "accepted"),
        )
        db.add(question)
        questions.append(question)

    await db.flush()

    # 3. 读取所有 review 文件并更新 question 审核状态
    review_files = []
    if subagent_dir.exists():
        review_files = sorted(subagent_dir.glob("review_*.json"))

    # 建立 question seq -> ORM 对象的映射，便于按题号更新
    question_by_seq: dict[int, Question] = {q.seq: q for q in questions}

    # 默认全部通过，后续按 review 结果修正
    for q in questions:
        q.review_passed = True

    for r_file in review_files:
        try:
            r_data = json.loads(r_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if r_data.get("status") != "success":
            continue

        # 被拒绝的题目：review_passed = False
        for rejected in r_data.get("rejected", []):
            seq = rejected.get("seq")
            if seq is not None and seq in question_by_seq:
                question_by_seq[seq].review_passed = False

        # 自动修复的题目：review_passed = True
        for fixed in r_data.get("auto_fixed", []):
            seq = fixed.get("seq")
            if seq is not None and seq in question_by_seq:
                question_by_seq[seq].review_passed = True

    await db.flush()

    # 4. 统计结果
    total_questions = len(questions)
    abandoned_count = sum(1 for q in questions if q.status == "abandoned")

    return {
        "total_plan_items": len(plan_items),
        "total_questions": total_questions,
        "abandoned": abandoned_count,
    }

"""审查 agent（reviewer）。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import llm_client
from app.agents.prompts import load_prompt
from app.models.question import Question
from app.orchestration.events import EventBus
from app.orchestration.state_machine import Stage


class ReviewResult:
    def __init__(
        self,
        checked: int,
        passed: int,
        rejected: list[dict[str, Any]],
        auto_fixed: list[dict[str, Any]],
    ) -> None:
        self.checked = checked
        self.passed = passed
        self.rejected = rejected
        self.auto_fixed = auto_fixed


async def run_reviewer(
    db: AsyncSession,
    job_id: uuid.UUID,
    bus: EventBus,
    questions: list[Question],
    plan_items_map: dict[int, Any],
    model: str = "gpt-4o",
) -> ReviewResult:
    """执行审查 agent，返回审查结果。"""

    # 构造审查输入
    questions_with_plan = []
    for q in questions:
        plan = plan_items_map.get(q.seq)
        questions_with_plan.append({
            "seq": q.seq,
            "question_type": q.question_type,
            "stem": q.stem,
            "options": q.options,
            "answer": q.answer,
            "sub_questions": q.sub_questions,
            "sub_answers": q.sub_answers,
            "plan_item": {
                "knowledge_point": plan.knowledge_point if plan else "",
                "exam_direction": plan.exam_direction if plan else "",
                "difficulty": plan.difficulty if plan else "",
                "question_type": plan.question_type if plan else "",
            } if plan else None,
        })

    template = load_prompt("reviewer")
    prompt = template.format(questions_with_plan_items=json.dumps(questions_with_plan, ensure_ascii=False, indent=2))

    response = await llm_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": "你是一位严格的试卷审查专家，擅长发现题目中的问题并给出结构化反馈。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    choice = response.choices[0]
    content = choice.message.content or ""

    # 解析审查结果
    rejected: list[dict[str, Any]] = []
    auto_fixed: list[dict[str, Any]] = []

    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            result_data = json.loads(content[start:end])
            rejected = result_data.get("rejected", [])
            auto_fixed = result_data.get("auto_fixed", [])
    except (json.JSONDecodeError, ValueError):
        pass

    # 更新 question 状态
    rejected_seqs = {r["seq"] for r in rejected}
    for q in questions:
        if q.seq in rejected_seqs:
            q.status = "draft"
            q.review_passed = False
        else:
            q.review_passed = True
            q.status = "accepted"

    await db.flush()

    # 发送 review_result 事件
    await bus.emit(
        job_id,
        "review_result",
        {
            "checked": len(questions),
            "passed": len(questions) - len(rejected),
            "rejected": rejected,
            "auto_fixed": auto_fixed,
        },
        stage=Stage.reviewing.value,
    )
    await db.commit()

    return ReviewResult(
        checked=len(questions),
        passed=len(questions) - len(rejected),
        rejected=rejected,
        auto_fixed=auto_fixed,
    )

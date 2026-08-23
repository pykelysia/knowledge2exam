"""规划 agent（planner）。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import llm_client
from app.agents.prompts import load_prompt
from app.models.plan import PlanItem
from app.orchestration.events import EventBus
from app.orchestration.state_machine import Stage


class PlannerResult:
    def __init__(self, plan_items: list[dict[str, Any]]) -> None:
        self.plan_items = plan_items


async def run_planner(
    db: AsyncSession,
    job_id: uuid.UUID,
    bus: EventBus,
    context: dict[str, Any],
) -> list[PlanItem]:
    """执行规划 agent，返回 PlanItem ORM 对象列表。"""

    # 1. 构造提示词
    template = load_prompt("planner")
    prompt = template.format(
        duration_minutes=context.get("duration_minutes", 100),
        past_papers_full_text_or_none=context.get("past_papers_full_text_or_none") or "无",
        keypoint_list_or_none=context.get("keypoint_list_or_none") or "无",
        extra_requirement_or_none=context.get("extra_requirement_or_none") or "无",
        shared_library_summary=context.get("shared_library_summary") or "无共享库内容",
    )

    # 2. 调用 LLM
    response = await llm_client.chat(
        model=context.get("planner_model", "gpt-4o"),
        messages=[
            {"role": "system", "content": "你是一位专业的试卷规划专家，擅长根据教学材料设计合理的试卷蓝图。请始终输出 JSON 数组格式。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )

    # 3. 解析 LLM 返回的 plan_item 列表
    choice = response.choices[0]
    plan_items_raw: list[dict[str, Any]] = []

    # 优先从文本解析 JSON 数组
    content = choice.message.content or ""
    try:
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            plan_items_raw = json.loads(content[start:end])
    except (json.JSONDecodeError, ValueError):
        pass

    # 如果 LLM 使用了 tool_calls（某些模型可能把 JSON 包装在 tool call 里），也尝试解析
    if not plan_items_raw and choice.message.tool_calls:
        for tool_call in choice.message.tool_calls:
            try:
                args = json.loads(tool_call.function.arguments)
                if isinstance(args, list):
                    plan_items_raw = args
                    break
            except (json.JSONDecodeError, ValueError):
                continue

    if not plan_items_raw:
        raise RuntimeError("规划 agent 未产出任何 plan_item")

    # 4. 补全字段并落盘
    plan_items: list[PlanItem] = []
    seq = 0
    for item in plan_items_raw:
        seq += 1
        plan = PlanItem(
            job_id=job_id,
            seq=seq,
            question_type=item.get("question_type", "choice"),
            knowledge_point=item.get("knowledge_point", ""),
            exam_direction=item.get("exam_direction", ""),
            difficulty=item.get("difficulty", "medium"),
            reference_source=item.get("reference_source"),
        )
        db.add(plan)
        plan_items.append(plan)

    await db.flush()

    # 5. 发送 plan_ready 事件
    distribution: dict[str, int] = {}
    for p in plan_items:
        distribution[p.question_type] = distribution.get(p.question_type, 0) + 1

    await bus.emit(
        job_id,
        "plan_ready",
        {
            "total": len(plan_items),
            "distribution": distribution,
            "reference_used": context.get("reference_used", "default_template"),
            "duration_minutes": context.get("duration_minutes", 100),
        },
        stage=Stage.planning.value,
    )

    return plan_items

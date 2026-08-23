"""出题 agent（choice / blank / short_answer）。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import llm_client
from app.agents.prompts import load_prompt
from app.agents.tools import (
    ALL_TOOLS,
    TOOL_NAME_TO_TYPE,
    validate_tool_call,
    tool_name_to_question_type,
)
from app.core.exceptions import AppException, ErrorCode
from app.models.question import Question, RetryLog


class GenerationError(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.GENERATION_EXHAUSTED, message)


async def _call_writer_llm(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tool_name: str,
    need_explanation: bool,
    temperature: float = 0.7,
) -> dict:
    """调用 LLM 生成单道题目，返回解析后的 tool 调用参数。"""

    # 过滤出当前题型对应的 tool
    tools = [t for t in ALL_TOOLS if t["function"]["name"] == tool_name]

    response = await llm_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=tools,
        temperature=temperature,
    )

    choice = response.choices[0]
    if not choice.message.tool_calls:
        raise RuntimeError(f"agent 未通过 tool 提交题目（模型返回: {choice.message.content}）")

    tool_call = choice.message.tool_calls[0]
    if tool_call.function.name != tool_name:
        raise RuntimeError(f"agent 调用了错误的 tool: {tool_call.function.name}")

    args = json.loads(tool_call.function.arguments)

    # 根据 need_explanation 决定是否校验 explanation 字段
    if not need_explanation and "explanation" in args:
        del args["explanation"]

    # schema 层校验
    validate_tool_call(tool_name, args)

    return args


async def generate_single_question(
    db: AsyncSession,
    job_id: uuid.UUID,
    plan_item: Any,
    need_explanation: bool,
    model: str,
    knowledge_context: str,
    retry_count: int = 0,
) -> Question:
    """为单个 plan_item 生成题目（含重试）。"""

    tool_name = {
        "choice": "create_choice_question",
        "blank": "create_blank_question",
        "short_answer": "create_short_answer_question",
    }[plan_item.question_type]

    question_type_label = {
        "choice": "选择题",
        "blank": "填空题",
        "short_answer": "简答题",
    }[plan_item.question_type]

    system_prompt = (
        f"你负责生成试卷中的第 {plan_item.seq} 题（{question_type_label}）。\n"
        f"规划信息（仅供理解，不作为工具参数）：\n"
        f"knowledge_point: {plan_item.knowledge_point}\n"
        f"exam_direction: {plan_item.exam_direction}\n"
        f"difficulty: {plan_item.difficulty}\n"
        f"reference_source: {plan_item.reference_source or '无'}\n\n"
        f"相关知识库内容：\n{knowledge_context}\n\n"
        f"若 reference_source 指向简单计算题，仅修改数值；概念性内容不适用于简答题的"
        f"逐字迁移例外（该例外仅适用于概念性填空题）。\n"
        f"{'需附带解析。' if need_explanation else '不要生成解析字段。'}\n"
        f"若连续 3 次未通过审核，停止重试并上报 request_replan。"
    )

    user_prompt = f"请根据上述规划信息生成第 {plan_item.seq} 题的题目内容。"

    try:
        args = await _call_writer_llm(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_name=tool_name,
            need_explanation=need_explanation,
        )
    except Exception as exc:
        # 写入 retry_log（LLM 失败不计入 retry_count，因为没有 question 对象）
        log = RetryLog(
            question_id=None,
            plan_item_id=plan_item.id,
            attempt=retry_count,
            reason="generation_failed",
            counted=False,
            detail={"error": str(exc), "model": model},
        )
        db.add(log)
        await db.flush()
        raise GenerationError(f"生成第 {plan_item.seq} 题失败: {exc}") from exc

    # 构造 Question ORM
    q = Question(
        job_id=job_id,
        plan_item_id=plan_item.id,
        seq=plan_item.seq,
        question_type=plan_item.question_type,
        stem=args.get("stem", ""),
        options=args.get("options"),
        answer=args.get("answer", ""),
        sub_questions=args.get("sub_questions"),
        sub_answers=args.get("sub_answers"),
        explanation=args.get("explanation") if need_explanation else None,
        retry_count=retry_count,
        status="accepted",
    )
    db.add(q)
    await db.flush()
    return q


async def generate_choice_all(
    db: AsyncSession,
    job_id: uuid.UUID,
    plan_items: list[Any],
    need_explanation: bool,
    model: str,
    knowledge_context: str,
    bus: Any,
    retry_count_map: dict[uuid.UUID, int] | None = None,
) -> list[Question]:
    """选择题 agent：包全部选择题。"""
    questions: list[Question] = []
    for plan_item in plan_items:
        q = await generate_single_question(
            db=db,
            job_id=job_id,
            plan_item=plan_item,
            need_explanation=need_explanation,
            model=model,
            knowledge_context=knowledge_context,
            retry_count=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
        )
        questions.append(q)
        if bus:
            await bus.emit(
                job_id,
                "question_completed",
                {
                    "seq": q.seq,
                    "question_type": "choice",
                    "completed": len(questions),
                    "total": len(plan_items),
                },
                stage=None,
            )
            await db.commit()
    return questions


async def generate_blank_all(
    db: AsyncSession,
    job_id: uuid.UUID,
    plan_items: list[Any],
    need_explanation: bool,
    model: str,
    knowledge_context: str,
    bus: Any,
    retry_count_map: dict[uuid.UUID, int] | None = None,
) -> list[Question]:
    """填空题 agent：包全部填空题。"""
    questions: list[Question] = []
    for plan_item in plan_items:
        q = await generate_single_question(
            db=db,
            job_id=job_id,
            plan_item=plan_item,
            need_explanation=need_explanation,
            model=model,
            knowledge_context=knowledge_context,
            retry_count=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
        )
        questions.append(q)
        if bus:
            await bus.emit(
                job_id,
                "question_completed",
                {
                    "seq": q.seq,
                    "question_type": "blank",
                    "completed": len(questions),
                    "total": len(plan_items),
                },
                stage=None,
            )
            await db.commit()
    return questions


async def generate_short_single(
    db: AsyncSession,
    job_id: uuid.UUID,
    plan_item: Any,
    need_explanation: bool,
    model: str,
    knowledge_context: str,
    bus: Any,
    retry_count_map: dict[uuid.UUID, int] | None = None,
) -> Question:
    """简答题 agent：每题一个 agent。"""
    q = await generate_single_question(
        db=db,
        job_id=job_id,
        plan_item=plan_item,
        need_explanation=need_explanation,
        model=model,
        knowledge_context=knowledge_context,
        retry_count=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
    )
    if bus:
        await bus.emit(
            job_id,
            "question_completed",
            {
                "seq": q.seq,
                "question_type": "short_answer",
                "completed": 1,
                "total": 1,
            },
            stage=None,
        )
        await db.commit()
    return q

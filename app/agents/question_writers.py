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
from app.core.debug_log import log_step
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
    import time

    # 过滤出当前题型对应的 tool
    tools = [t for t in ALL_TOOLS if t["function"]["name"] == tool_name]

    llm_t0 = time.perf_counter()
    response = await llm_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=tools,
        temperature=temperature,
    )
    llm_elapsed = (time.perf_counter() - llm_t0) * 1000

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


async def _call_writer_llm_batch(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tool_name: str,
    plan_items: list[Any],
    need_explanation: bool,
    temperature: float = 0.7,
) -> list[dict]:
    """批量调用 LLM 生成多道题目，返回解析后的 tool 调用参数列表。"""
    import time

    tools = [t for t in ALL_TOOLS if t["function"]["name"] == tool_name]

    llm_t0 = time.perf_counter()
    response = await llm_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=tools,
        temperature=temperature,
    )
    llm_elapsed = (time.perf_counter() - llm_t0) * 1000

    choice = response.choices[0]

    # 解析所有 tool calls（支持并行 function calling）
    if not choice.message.tool_calls:
        raise RuntimeError(f"agent 未通过 tool 提交题目（模型返回: {choice.message.content}）")

    results: list[dict] = []
    for tool_call in choice.message.tool_calls:
        if tool_call.function.name != tool_name:
            continue

        try:
            args = json.loads(tool_call.function.arguments)
        except (json.JSONDecodeError, ValueError):
            continue

        # 根据 need_explanation 决定是否移除 explanation 字段
        if not need_explanation and "explanation" in args:
            del args["explanation"]

        # schema 层校验
        try:
            validate_tool_call(tool_name, args)
        except Exception:
            continue

        results.append(args)

    if not results:
        raise RuntimeError("批量调用未返回任何有效的 tool 调用")

    # debug 日志由调用方（generate_*_all）统一记录，避免重复
    return results


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
    """选择题 agent：包全部选择题（批量生成）。"""

    tool_name = "create_choice_question"
    question_type_label = "选择题"

    # 构造批量 system prompt
    plan_items_meta = "\n".join(
        f"第 {p.seq} 题：knowledge_point={p.knowledge_point}, "
        f"exam_direction={p.exam_direction}, difficulty={p.difficulty}, "
        f"reference_source={p.reference_source or '无'}"
        for p in plan_items
    )

    system_prompt = (
        f"你负责生成本次试卷中全部{question_type_label}。"
        f"请在一次回复中通过多个 {tool_name} tool call 提交所有题目。\n\n"
        f"以下是分配给你的规划项列表（仅供理解出题方向，不会作为工具参数）：\n"
        f"{plan_items_meta}\n\n"
        f"相关知识库内容：\n{knowledge_context}\n\n"
        f"对每一项：\n"
        f"1. 检索该知识点相关的知识库内容。\n"
        f"2. 若 reference_source 存在，按以下规则处理：\n"
        f"   - 简单计算题：保留解法路径，仅修改数值\n"
        f"   - 概念性填空题：可原封不动迁移\n"
        f"   - 其他：必须实质改写，不能与原题高度相似\n"
        f"3. 通过 {tool_name} 提交题目。{'需附带解析。' if need_explanation else '不要生成解析字段。'}\n"
        f"4. 全部题目生成后，检查这批题目之间选项风格是否雷同、答案分布是否失衡"
        f"（如四个选项字母中某个出现频率异常高）。\n\n"
        f"若某题连续 3 次未通过审核，停止重试并上报 request_replan。\n"
        f"请确保返回的 tool call 数量与规划项数量一致。"
    )

    user_prompt = "请根据上述规划信息，为每道题目生成内容，通过 tool call 提交。"

    import time

    gen_t0 = time.perf_counter()
    try:
        args_list = await _call_writer_llm_batch(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_name=tool_name,
            plan_items=plan_items,
            need_explanation=need_explanation,
        )
    except Exception as exc:
        # 写入 retry_log
        for plan_item in plan_items:
            log = RetryLog(
                question_id=None,
                plan_item_id=plan_item.id,
                attempt=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
                reason="generation_failed",
                counted=False,
                detail={"error": str(exc), "model": model, "batch": True},
            )
            db.add(log)
        await db.flush()
        raise GenerationError(f"批量生成选择题失败: {exc}") from exc

    gen_elapsed = (time.perf_counter() - gen_t0) * 1000

    # 构造 Question ORM 列表
    questions: list[Question] = []
    for i, plan_item in enumerate(plan_items):
        if i < len(args_list):
            args = args_list[i]
        else:
            # 如果返回的 tool call 数量不足，用空参数填充
            args = {"stem": "", "options": {"A": "", "B": "", "C": "", "D": ""}, "answer": "A"}

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
            retry_count=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
            status="accepted",
        )
        db.add(q)
        questions.append(q)

    await db.flush()

    if bus:
        for q in questions:
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

    await log_step(
        job_id=str(job_id),
        name="generate_choice_all",
        stage="generating",
        input={"model": model, "plan_count": len(plan_items)},
        output={"generated": len(questions)},
        elapsed_ms=gen_elapsed,
    )

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
    """填空题 agent：包全部填空题（批量生成）。"""

    tool_name = "create_blank_question"
    question_type_label = "填空题"

    # 构造批量 system prompt
    plan_items_meta = "\n".join(
        f"第 {p.seq} 题：knowledge_point={p.knowledge_point}, "
        f"exam_direction={p.exam_direction}, difficulty={p.difficulty}, "
        f"reference_source={p.reference_source or '无'}"
        for p in plan_items
    )

    system_prompt = (
        f"你负责生成本次试卷中全部{question_type_label}。"
        f"请在一次回复中通过多个 {tool_name} tool call 提交所有题目。\n\n"
        f"以下是分配给你的规划项列表（仅供理解出题方向，不会作为工具参数）：\n"
        f"{plan_items_meta}\n\n"
        f"相关知识库内容：\n{knowledge_context}\n\n"
        f"对每一项：\n"
        f"1. 检索该知识点相关的知识库内容。\n"
        f"2. 若 reference_source 存在，按以下规则处理：\n"
        f"   - 简单计算题：保留解法路径，仅修改数值\n"
        f"   - 概念性填空题：可原封不动迁移\n"
        f"   - 其他：必须实质改写，不能与原题高度相似\n"
        f"3. 通过 {tool_name} 提交题目。{'需附带解析。' if need_explanation else '不要生成解析字段。'}\n"
        f"4. 全部题目生成后，检查这批题目之间风格是否雷同、答案分布是否失衡。\n\n"
        f"若某题连续 3 次未通过审核，停止重试并上报 request_replan。\n"
        f"请确保返回的 tool call 数量与规划项数量一致。"
    )

    user_prompt = "请根据上述规划信息，为每道题目生成内容，通过 tool call 提交。"

    import time

    gen_t0 = time.perf_counter()
    try:
        args_list = await _call_writer_llm_batch(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_name=tool_name,
            plan_items=plan_items,
            need_explanation=need_explanation,
        )
    except Exception as exc:
        # 写入 retry_log
        for plan_item in plan_items:
            log = RetryLog(
                question_id=None,
                plan_item_id=plan_item.id,
                attempt=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
                reason="generation_failed",
                counted=False,
                detail={"error": str(exc), "model": model, "batch": True},
            )
            db.add(log)
        await db.flush()
        raise GenerationError(f"批量生成填空题失败: {exc}") from exc

    gen_elapsed = (time.perf_counter() - gen_t0) * 1000

    # 构造 Question ORM 列表
    questions: list[Question] = []
    for i, plan_item in enumerate(plan_items):
        if i < len(args_list):
            args = args_list[i]
        else:
            args = {"stem": "", "answer": ""}

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
            retry_count=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
            status="accepted",
        )
        db.add(q)
        questions.append(q)

    await db.flush()

    if bus:
        for q in questions:
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

    await log_step(
        job_id=str(job_id),
        name="generate_blank_all",
        stage="generating",
        input={"model": model, "plan_count": len(plan_items)},
        output={"generated": len(questions)},
        elapsed_ms=gen_elapsed,
    )

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
    import time

    gen_t0 = time.perf_counter()
    q = await generate_single_question(
        db=db,
        job_id=job_id,
        plan_item=plan_item,
        need_explanation=need_explanation,
        model=model,
        knowledge_context=knowledge_context,
        retry_count=retry_count_map.get(plan_item.id, 0) if retry_count_map else 0,
    )
    gen_elapsed = (time.perf_counter() - gen_t0) * 1000

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

    await log_step(
        job_id=str(job_id),
        name="generate_short_single",
        stage="generating",
        input={"model": model, "seq": plan_item.seq},
        output={"seq": q.seq},
        elapsed_ms=gen_elapsed,
    )
    return q

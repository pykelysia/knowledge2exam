"""Writer subagent。

将结果写入 storage/jobs/{job_id}/subagent/question_{seq}_{type}.json。
支持内部 review 和重试。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from app.agents.llm import llm_client
from app.agents.prompts import load_prompt
from app.agents.reviewer_subagent import run_reviewer_subagent
from app.agents.subagent_io import write_subagent_result
from app.agents.base import SubAgentResult
from app.agents.tools import (
    ALL_TOOLS,
    validate_tool_call,
    tool_name_to_question_type,
)
from app.config import settings
from app.core.debug_log import log_step

logger = logging.getLogger(__name__)


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

    if not response.tool_calls:
        raise RuntimeError(f"agent 未通过 tool 提交题目（模型返回: {response.content}）")

    tool_call = response.tool_calls[0]

    def _get_tool_call_info(call: Any) -> tuple[str | None, str | None]:
        """兼容多种 tool_call 格式，返回 (name, arguments)。"""
        if isinstance(call, dict):
            func = call.get("function")
            if isinstance(func, dict):
                return func.get("name"), func.get("arguments", "{}")
            # 扁平化格式：兼容 arguments / args 两种字段名
            if "name" in call:
                args_raw = call.get("arguments") or call.get("args") or "{}"
                return call.get("name"), args_raw
            return None, None
        func = getattr(call, "function", None)
        if func is not None:
            return getattr(func, "name", None), getattr(func, "arguments", "{}")
        return getattr(call, "name", None), getattr(call, "arguments", "{}")

    actual_name, arguments_raw = _get_tool_call_info(tool_call)
    if not actual_name or actual_name != tool_name:
        raise RuntimeError(
            f"agent 调用了错误的 tool: {actual_name}；"
            f"原始 tool_calls={response.tool_calls!r}"
        )

    args = json.loads(arguments_raw or "{}")

    if not need_explanation and "explanation" in args:
        del args["explanation"]

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

    if not response.tool_calls:
        raise RuntimeError(f"agent 未通过 tool 提交题目（模型返回: {response.content}）")

    results: list[dict] = []

    # 记录原始 tool_calls 格式，便于诊断兼容问题
    try:
        raw_calls = []
        for tc in response.tool_calls:
            if isinstance(tc, dict):
                raw_calls.append(tc)
            else:
                raw_calls.append({
                    "type": type(tc).__name__,
                    "attrs": [a for a in dir(tc) if not a.startswith("_")],
                })
        logger.warning("批量生成 tool_calls 原始格式: %s", raw_calls)
    except Exception:
        pass

    for tool_call in response.tool_calls:

        def _get_tool_call_info(call: Any) -> tuple[str | None, str | None]:
            """兼容多种 tool_call 格式，返回 (name, arguments)。"""
            if isinstance(call, dict):
                func = call.get("function")
                if isinstance(func, dict):
                    return func.get("name"), func.get("arguments", "{}")
                if "name" in call:
                    return call.get("name"), call.get("arguments", "{}")
                return None, None
            func = getattr(call, "function", None)
            if func is not None:
                return getattr(func, "name", None), getattr(func, "arguments", "{}")
            return getattr(call, "name", None), getattr(call, "arguments", "{}")

        actual_name, arguments_raw = _get_tool_call_info(tool_call)
        if not actual_name or actual_name != tool_name:
            continue

        try:
            args = json.loads(arguments_raw or "{}")
        except (json.JSONDecodeError, ValueError):
            continue

        if not need_explanation and "explanation" in args:
            del args["explanation"]

        try:
            validate_tool_call(tool_name, args)
        except Exception:
            continue

        results.append(args)

    if not results:
        raise RuntimeError(
            "批量调用未返回任何有效的 tool 调用；"
            f"原始 tool_calls={response.tool_calls!r}"
        )

    return results


async def run_writer_subagent(
    job_id: uuid.UUID,
    plan_items: list[dict[str, Any]],
    knowledge_context: str,
    output_dir: Path,
    need_explanation: bool,
    review_mode: str,
    model: str | None = None,
    reviewer_model: str | None = None,
    max_retries: int | None = None,
) -> SubAgentResult:
    """执行 writer subagent，生成题目并写入文件。"""

    from app.config import get_agent_model

    used_model = model or get_agent_model("writer")
    used_reviewer_model = reviewer_model or get_agent_model("reviewer")
    retries = max_retries or settings.subagent_max_retries

    # 按题型分组
    choice_plans = [p for p in plan_items if p["question_type"] == "choice"]
    blank_plans = [p for p in plan_items if p["question_type"] == "blank"]
    short_plans = [p for p in plan_items if p["question_type"] == "short_answer"]

    all_questions = []
    errors = []

    # 生成选择题
    if choice_plans:
        for attempt in range(retries):
            try:
                plan_items_meta = "\n".join(
                    f"第 {p['seq']} 题：knowledge_point={p['knowledge_point']}, "
                    f"exam_direction={p['exam_direction']}, difficulty={p['difficulty']}, "
                    f"reference_source={p.get('reference_source') or '无'}"
                    for p in choice_plans
                )

                system_prompt = (
                    f"你负责生成本次试卷中全部选择题。"
                    f"请在一次回复中通过多个 create_choice_question tool call 提交所有题目。\n\n"
                    f"以下是分配给你的规划项列表（仅供理解出题方向，不会作为工具参数）：\n"
                    f"{plan_items_meta}\n\n"
                    f"相关知识库内容：\n{knowledge_context}\n\n"
                    f"对每一项：\n"
                    f"1. 检索该知识点相关的知识库内容。\n"
                    f"2. 若 reference_source 存在，按以下规则处理：\n"
                    f"   - 简单计算题：保留解法路径，仅修改数值\n"
                    f"   - 概念性填空题：可原封不动迁移\n"
                    f"   - 其他：必须实质改写，不能与原题高度相似\n"
                    f"3. 通过 create_choice_question 提交题目。{'需附带解析。' if need_explanation else '不要生成解析字段。'}\n"
                    f"4. 全部题目生成后，检查这批题目之间选项风格是否雷同、答案分布是否失衡"
                    f"（如四个选项字母中某个出现频率异常高）。\n\n"
                    f"若某题连续 3 次未通过审核，停止重试并上报 request_replan。\n"
                    f"请确保返回的 tool call 数量与规划项数量一致。"
                )

                user_prompt = "请根据上述规划信息，为每道题目生成内容，通过 tool call 提交。"

                args_list = await _call_writer_llm_batch(
                    model=used_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    tool_name="create_choice_question",
                    plan_items=choice_plans,
                    need_explanation=need_explanation,
                )

                # 构造题目数据
                for i, plan_item in enumerate(choice_plans):
                    if i < len(args_list):
                        args = args_list[i]
                    else:
                        args = {"stem": "", "options": {"A": "", "B": "", "C": "", "D": ""}, "answer": "A"}

                    question_data = {
                        "seq": plan_item["seq"],
                        "question_type": "choice",
                        "stem": args.get("stem", ""),
                        "options": args.get("options"),
                        "answer": args.get("answer", ""),
                        "explanation": args.get("explanation") if need_explanation else None,
                        "status": "accepted",
                    }
                    all_questions.append(question_data)

                break
            except Exception as exc:
                logger.warning("生成选择题失败（第%d次）: %s", attempt + 1, exc)
                if attempt >= retries - 1:
                    errors.append({
                        "type": "choice",
                        "error": f"生成选择题失败: {exc}",
                    })

    # 生成填空题
    if blank_plans:
        for attempt in range(retries):
            try:
                plan_items_meta = "\n".join(
                    f"第 {p['seq']} 题：knowledge_point={p['knowledge_point']}, "
                    f"exam_direction={p['exam_direction']}, difficulty={p['difficulty']}, "
                    f"reference_source={p.get('reference_source') or '无'}"
                    for p in blank_plans
                )

                system_prompt = (
                    f"你负责生成本次试卷中全部填空题。"
                    f"请在一次回复中通过多个 create_blank_question tool call 提交所有题目。\n\n"
                    f"以下是分配给你的规划项列表（仅供理解出题方向，不会作为工具参数）：\n"
                    f"{plan_items_meta}\n\n"
                    f"相关知识库内容：\n{knowledge_context}\n\n"
                    f"对每一项：\n"
                    f"1. 检索该知识点相关的知识库内容。\n"
                    f"2. 若 reference_source 存在，按以下规则处理：\n"
                    f"   - 简单计算题：保留解法路径，仅修改数值\n"
                    f"   - 概念性填空题：可原封不动迁移\n"
                    f"   - 其他：必须实质改写，不能与原题高度相似\n"
                    f"3. 通过 create_blank_question 提交题目。{'需附带解析。' if need_explanation else '不要生成解析字段。'}\n"
                    f"4. 全部题目生成后，检查这批题目之间风格是否雷同、答案分布是否失衡。\n\n"
                    f"若某题连续 3 次未通过审核，停止重试并上报 request_replan。\n"
                    f"请确保返回的 tool call 数量与规划项数量一致。"
                )

                user_prompt = "请根据上述规划信息，为每道题目生成内容，通过 tool call 提交。"

                args_list = await _call_writer_llm_batch(
                    model=used_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    tool_name="create_blank_question",
                    plan_items=blank_plans,
                    need_explanation=need_explanation,
                )

                for i, plan_item in enumerate(blank_plans):
                    if i < len(args_list):
                        args = args_list[i]
                    else:
                        args = {"stem": "", "answer": ""}

                    question_data = {
                        "seq": plan_item["seq"],
                        "question_type": "blank",
                        "stem": args.get("stem", ""),
                        "answer": args.get("answer", ""),
                        "explanation": args.get("explanation") if need_explanation else None,
                        "status": "accepted",
                    }
                    all_questions.append(question_data)

                break
            except Exception as exc:
                logger.warning("生成填空题失败（第%d次）: %s", attempt + 1, exc)
                if attempt >= retries - 1:
                    errors.append({
                        "type": "blank",
                        "error": f"生成填空题失败: {exc}",
                    })

    # 生成简答题（每题单独生成）
    for plan_item in short_plans:
        for attempt in range(retries):
            try:
                tool_name = "create_short_answer_question"
                question_type_label = "简答题"

                system_prompt = (
                    f"你负责生成试卷中的第 {plan_item['seq']} 题（{question_type_label}）。\n"
                    f"规划信息（仅供理解，不作为工具参数）：\n"
                    f"knowledge_point: {plan_item['knowledge_point']}\n"
                    f"exam_direction: {plan_item['exam_direction']}\n"
                    f"difficulty: {plan_item['difficulty']}\n"
                    f"reference_source: {plan_item.get('reference_source') or '无'}\n\n"
                    f"相关知识库内容：\n{knowledge_context}\n\n"
                    f"若 reference_source 指向简单计算题，仅修改数值；概念性内容不适用于简答题的"
                    f"逐字迁移例外（该例外仅适用于概念性填空题）。\n"
                    f"{'需附带解析。' if need_explanation else '不要生成解析字段。'}\n"
                    f"若连续 3 次未通过审核，停止重试并上报 request_replan。"
                )

                user_prompt = f"请根据上述规划信息生成第 {plan_item['seq']} 题的题目内容。"

                args = await _call_writer_llm(
                    model=used_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    tool_name=tool_name,
                    need_explanation=need_explanation,
                )

                question_data = {
                    "seq": plan_item["seq"],
                    "question_type": "short_answer",
                    "stem": args.get("stem", ""),
                    "sub_questions": args.get("sub_questions"),
                    "answer": args.get("answer", ""),
                    "sub_answers": args.get("sub_answers"),
                    "explanation": args.get("explanation") if need_explanation else None,
                    "status": "accepted",
                }
                all_questions.append(question_data)
                break
            except Exception as exc:
                logger.warning("生成第%d题失败（第%d次）: %s", plan_item["seq"], attempt + 1, exc)
                if attempt >= retries - 1:
                    errors.append({
                        "type": "short_answer",
                        "seq": plan_item["seq"],
                        "error": f"生成第{plan_item['seq']}题失败: {exc}",
                    })

    # 内部 review（如果启用完整审核模式）
    if review_mode == "full" and all_questions:
        try:
            review_result = await run_reviewer_subagent(
                job_id=job_id,
                questions=all_questions,
                output_dir=output_dir,
                review_mode=review_mode,
                model=used_reviewer_model,
            )

            # 根据 review 结果更新题目状态
            if review_result.status == "success":
                rejected_seqs = {r["seq"] for r in review_result.rejected}
                for q in all_questions:
                    if q["seq"] in rejected_seqs:
                        q["status"] = "rejected"
        except Exception as exc:
            logger.warning("内部 review 失败: %s", exc)

    # 写入文件
    for q in all_questions:
        result_data = {
            "type": "question",
            "seq": q["seq"],
            "question_type": q["question_type"],
            "status": q["status"],
            "question": q,
            "error": None,
            "replan_reason": None,
            "retry_count": 0,
        }
        write_subagent_result(job_id, q["seq"], f"question_{q['question_type']}", result_data)

    # 如果有错误，返回 needs_replan
    if errors:
        return SubAgentResult(
            status="needs_replan",
            error="部分题目生成失败",
            replan_requests=[
                {
                    "seq": e.get("seq", 0),
                    "question_type": e["type"],
                    "knowledge_point": "",
                    "exam_direction": "",
                    "failure_reason": e["error"],
                    "retry_count": retries,
                }
                for e in errors
            ],
        )

    return SubAgentResult(
        status="success",
        error=None,
    )

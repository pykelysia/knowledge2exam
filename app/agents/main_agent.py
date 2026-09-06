"""MainAgent 基于 LangGraph StateGraph 实现。

架构：
- LLM 节点：调用 MainAgent LLM，可能返回 tool_calls
- Tool 节点：执行 subAgent 工具（同步执行）
- 条件边：如果 LLM 返回 tool_calls，则执行工具；否则结束
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any
from typing_extensions import Annotated

from langchain_core.tools import tool
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.agents.llm import llm_client
from app.agents.planner_subagent import run_planner_subagent
from app.agents.writer_subagent import run_writer_subagent
from app.agents.reviewer_subagent import run_reviewer_subagent
from app.agents.subagent_io import get_subagent_dir, read_all_subagent_results
from app.agents.base import SubAgentResult
from app.config import settings
from app.core.debug_log import log_step

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MainAgent State（LangGraph 1.x 要求 TypedDict，list 字段需要 reducer）
# ---------------------------------------------------------------------------

class MainAgentState(TypedDict, total=False):
    """MainAgent 状态：消息历史 + 业务上下文。"""
    messages: Annotated[list[dict[str, Any]], add_messages]
    job_id: str
    context: dict[str, Any]
    subagent_results: list[Any]
    final_result: Any
    current_step: str


# ---------------------------------------------------------------------------
# MainAgent Tools（将 SubAgent 包装为 LangChain tools）
# ---------------------------------------------------------------------------

@tool
async def plan_exam(
    job_id: str,
    context: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    """生成考试蓝图（plan items）。

    Args:
        job_id: 任务 ID
        context: 上下文信息（往期试卷、知识点、要求等）
        model: 可选的模型名称，为空则使用 context 中的 planner_model

    Returns:
        {"status": "success|failed", "plan_items": [...], "error": null}
    """
    output_dir = get_subagent_dir(job_id)
    result = await run_planner_subagent(
        job_id=uuid.UUID(job_id),
        context=context,
        output_dir=output_dir,
        model=model,
    )

    # 从文件中读取 plan_items，以便 LLM 后续工具调用使用
    plan_items: list[dict[str, Any]] = []
    if result.status == "success":
        plan_files = read_all_subagent_results(job_id, "plan")
        for plan_data in plan_files:
            if plan_data.get("status") == "success":
                plan_items.extend(plan_data.get("plan_items", []))

    return {
        "status": result.status,
        "error": result.error,
        "plan_items": plan_items,
        "replan_requests": result.replan_requests,
    }


@tool
async def generate_questions(
    job_id: str,
    plan_items: list[dict[str, Any]],
    knowledge_context: str = "",
    need_explanation: bool = False,
    review_mode: str = "format",
    model: str | None = None,
    reviewer_model: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """为给定的 plan items 生成题目，内部自动 review 和重试。

    Args:
        job_id: 任务 ID
        plan_items: plan items 列表
        knowledge_context: 知识库上下文
        need_explanation: 是否需要解析
        review_mode: "full"（完整审核）或 "format"（仅格式审核）
        model: 可选的模型名称，为空则使用 context 中的 writer_model
        reviewer_model: 可选的 reviewer 模型名称
        max_retries: 最大重试次数

    Returns:
        {"status": "success|failed|needs_replan", "questions": [...], "error": null}
    """
    output_dir = get_subagent_dir(job_id)
    result = await run_writer_subagent(
        job_id=uuid.UUID(job_id),
        plan_items=plan_items,
        knowledge_context=knowledge_context,
        output_dir=output_dir,
        need_explanation=need_explanation,
        review_mode=review_mode,
        model=model,
        reviewer_model=reviewer_model,
        max_retries=max_retries,
    )

    return {
        "status": result.status,
        "error": result.error,
        "replan_requests": result.replan_requests,
    }


@tool
async def review_questions(
    job_id: str,
    questions: list[dict[str, Any]],
    review_mode: str = "format",
    model: str | None = None,
) -> dict[str, Any]:
    """审核已生成的题目，返回审核结果。

    Args:
        job_id: 任务 ID
        questions: 题目列表
        review_mode: "full"（完整审核）或 "format"（仅格式审核）
        model: 可选的模型名称，为空则使用 context 中的 reviewer_model

    Returns:
        {"status": "success", "passed": 8, "rejected": [...], "auto_fixed": [...]}
    """
    output_dir = get_subagent_dir(job_id)
    result = await run_reviewer_subagent(
        job_id=uuid.UUID(job_id),
        questions=questions,
        output_dir=output_dir,
        review_mode=review_mode,
        model=model,
    )

    return {
        "status": result.status,
        "error": result.error,
    }


main_agent_tools = [plan_exam, generate_questions, review_questions]


# ---------------------------------------------------------------------------
# LLM 节点
# ---------------------------------------------------------------------------

async def call_main_agent_llm(state: MainAgentState) -> dict[str, Any]:
    """LLM 节点：根据当前状态决定下一步动作。"""

    system_prompt = """你是考试生成系统的 MainAgent，负责调度 subagent 完成出题任务。

可用工具：
- plan_exam: 生成考试蓝图（plan items）
- generate_questions: 为 plan items 生成题目（内部自动 review 和重试）
- review_questions: 审核已生成的题目

工作流程：
1. 首先调用 plan_exam 生成考试蓝图
2. 然后调用 generate_questions 为每个 plan item 生成题目
   - review_mode: 根据 context.enable_review 决定，为 true 时使用 "full"，否则使用 "format"
   - need_explanation: 根据 context.need_explanation 决定
   - max_retries: 使用 context.max_retries
3. 如果需要额外审核，调用 review_questions
4. 最终汇总结果，返回 success/failed 和 replan_requests

每个 subagent 会将结果写入文件，你只需要关注返回的状态摘要。"""

    messages = [
        {"role": "system", "content": system_prompt},
        *state.get("messages", []),
    ]

    response = await llm_client.chat(
        model=settings.main_agent_model,
        messages=messages,
        tools=main_agent_tools,
        temperature=0.3,
    )

    return {
        "messages": [response],
    }


# ---------------------------------------------------------------------------
# 图定义
# ---------------------------------------------------------------------------

def build_main_agent_graph() -> StateGraph:
    """构建 MainAgent 图：LLM 决策 -> Tool 执行 -> LLM 汇总。"""

    builder = StateGraph(MainAgentState)

    # LLM 节点：调用 MainAgent LLM，可能返回 tool_calls
    builder.add_node("llm_agent", call_main_agent_llm)

    # Tool 节点：执行 subAgent 工具（异步执行）
    builder.add_node("subagent_tools", ToolNode(main_agent_tools))

    # 条件边：如果 LLM 返回 tool_calls，则执行工具；否则结束
    def _has_tool_calls(state: MainAgentState) -> str:
        messages = state.get("messages") or []
        if not messages:
            return "end"
        last = messages[-1]
        # LangGraph 1.x 的 messages 是 BaseMessage 列表
        tool_calls = getattr(last, "tool_calls", None) or (last.get("tool_calls") if isinstance(last, dict) else None)
        return "tools" if tool_calls else "end"

    builder.add_conditional_edges(
        "llm_agent",
        _has_tool_calls,
        path_map={
            "tools": "subagent_tools",
            "end": END,
        },
    )

    # 工具执行完回到 LLM 节点，让 LLM 根据工具返回结果继续决策
    builder.add_edge("subagent_tools", "llm_agent")

    # LangGraph 1.x 推荐显式 add_edge(START, entry)
    builder.add_edge(START, "llm_agent")

    return builder.compile()


# ---------------------------------------------------------------------------
# 入口函数
# ---------------------------------------------------------------------------

async def run_main_agent(
    job_id: uuid.UUID,
    context: dict[str, Any],
) -> dict[str, Any]:
    """执行 MainAgent，返回最终结果。"""

    graph = build_main_agent_graph()

    initial_state: MainAgentState = {
        "messages": [
            {
                "role": "user",
                "content": f"请为 job {job_id} 生成试卷。context: {json.dumps(context, ensure_ascii=False)}",
            }
        ],
        "job_id": str(job_id),
        "context": context,
        "subagent_results": [],
        "final_result": None,
        "current_step": "start",
    }

    import time

    t0 = time.perf_counter()
    try:
        result = await graph.ainvoke(initial_state)
        elapsed = (time.perf_counter() - t0) * 1000

        # 防御性检查：ainvoke 可能返回 None（LangGraph 在某些版本/条件下）
        if result is None:
            logger.error(
                "MainAgent 图执行返回 None，initial_state.keys=%s",
                list(initial_state.keys()),
            )
            await log_step(
                job_id=str(job_id),
                name="run_main_agent",
                step="result",
                stage="main_agent",
                input={"job_id": str(job_id)},
                output={"error": "ainvoke returned None"},
                elapsed_ms=elapsed,
            )
            return {
                "success": False,
                "error": "MainAgent 图执行返回 None，请检查 LLM 配置和 LangGraph 版本",
                "total": 0,
                "abandoned": 0,
                "replan_requests": [],
            }

        # 从最后一条消息提取最终结果
        final_result = result.get("final_result")
        if not final_result:
            messages = result.get("messages", [])
            if messages:
                last_msg = messages[-1]
                content = getattr(last_msg, "content", None) or ""
                if isinstance(content, str):
                    try:
                        start = content.find("{")
                        end = content.rfind("}") + 1
                        if start >= 0 and end > start:
                            final_result = json.loads(content[start:end])
                    except (json.JSONDecodeError, ValueError):
                        pass

        await log_step(
            job_id=str(job_id),
            name="run_main_agent",
            step="result",
            stage="main_agent",
            input={"job_id": str(job_id)},
            output={"final_result": final_result},
            elapsed_ms=elapsed,
        )

        return final_result or {
            "success": True,
            "total": 0,
            "abandoned": 0,
            "replan_requests": [],
        }
    except Exception as exc:
        logger.error("MainAgent 执行失败: %s", exc, exc_info=True)
        return {
            "success": False,
            "error": str(exc),
            "total": 0,
            "abandoned": 0,
            "replan_requests": [],
        }

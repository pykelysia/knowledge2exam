"""ReAct agent 入口：意图提取 → 组装 → 循环 → 汇总。

对应架构图：
    用户文本输入 → [意图提取] ─┐
    系统预置提示词 ────────────┤→ [ReAct agent ⇄ tool use] → 决策（结束）
    skill system + 知识库 ─────┘                              ↓
                                              ExamResult 交回编排层入库渲染
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage

from app.agents.intent import extract_intent
from app.agents.llm import get_chat_model
from app.agents.prompts import load_prompt, render_prompt
from app.agents.schemas import AgentHooks, AgentSummary, ExamResult
from app.agents.skills import SkillLoader
from app.agents.tools import AgentContext, build_agent_tools, finalize
from app.agents.workspace import Workspace
from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.storage import storage
from app.retrieval.vector_store import PgVectorStore

logger = logging.getLogger(__name__)

_INTENT_MATERIALS = ("materials/keypoints.md", "materials/requirements.md")


async def run_exam_agent(
    job_id: UUID,
    context: dict,
    hooks: AgentHooks | None = None,
) -> ExamResult:
    """执行试卷生成 agent，返回结构化结果。

    context 字段（由编排层构造）：
        duration_minutes / need_explanation / max_retries /
        school_id / course_id / upload_ids
    预处理阶段应已把用户材料写入工作区 materials/ 目录。
    """
    hooks = hooks or AgentHooks()
    workspace = Workspace(storage, prefix=f"jobs/{job_id}/agent")

    ctx = AgentContext(
        job_id=job_id,
        workspace=workspace,
        skills=SkillLoader(settings.skills_dir),
        vector_store=PgVectorStore(AsyncSessionLocal),
        hooks=hooks,
        school_id=context.get("school_id"),
        course_id=context.get("course_id"),
        upload_ids=list(context.get("upload_ids") or []),
        need_explanation=bool(context.get("need_explanation")),
        max_retries=int(context.get("max_retries", settings.agent_max_retries)),
    )

    model = get_chat_model()

    # ---------- 意图提取 ----------
    intent = await extract_intent(
        model,
        duration_minutes=int(context.get("duration_minutes", 100)),
        keypoint_list=await _read_optional_material(workspace, "materials/keypoints.md"),
        extra_requirement=await _read_optional_material(workspace, "materials/requirements.md"),
    )

    # ---------- 组装 ReAct agent ----------
    materials = await workspace.list_dir("materials")
    material_list = (
        "\n".join(f"- materials/{name}" for name in materials) if materials else "（无材料）"
    )
    duration = int(context.get("duration_minutes", 100))
    system_prompt = render_prompt(
        load_prompt("system"),
        duration_minutes=str(duration),
        need_explanation_label="需要解析" if ctx.need_explanation else "不需要解析",
        intent=json.dumps(intent.model_dump(), ensure_ascii=False, indent=2),
        material_list=material_list,
        explanation_rule=(
            "每题必须携带 explanation 解析字段。"
            if ctx.need_explanation
            else "不要生成 explanation 字段。"
        ),
        max_retries=str(ctx.max_retries),
        skill_index=ctx.skills.index_prompt(),
    )

    agent = create_agent(
        model,
        tools=build_agent_tools(ctx),
        system_prompt=system_prompt,
        response_format=ToolStrategy(AgentSummary),
    )

    # ---------- 运行循环 ----------
    completed_normally = True
    summary_text = ""
    try:
        state = await agent.ainvoke(
            {"messages": [HumanMessage(content=(
                "请开始组卷。先阅读材料与技能，规划蓝图后逐题完成，"
                "全部完成后输出总结并结束。"
            ))]},
            config={"recursion_limit": settings.agent_recursion_limit},
        )
        structured = state.get("structured_response")
        if isinstance(structured, AgentSummary):
            summary_text = structured.summary
    except Exception as exc:
        completed_normally = False
        logger.exception("agent 循环异常中断（job=%s）", job_id)
        await hooks.fire_warning(f"agent 循环中断：{exc}；以已产出内容收尾")

    return await finalize(
        ctx,
        intent=intent,
        summary=summary_text,
        completed_normally=completed_normally,
    )


async def _read_optional_material(workspace: Workspace, rel: str) -> str | None:
    """读取可选材料文件，不存在返回 None。"""
    try:
        return await workspace.read(rel)
    except Exception:
        return None

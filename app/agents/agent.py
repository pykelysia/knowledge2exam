"""ReAct agent 入口：意图提取 → 组装 → 循环 → 渲染兜底 → 汇总。

对应架构图：
    用户文本输入 → [意图提取] ─┐
    系统预置提示词 ────────────┤→ [ReAct agent ⇄ tool use] → 决策（结束）
    skill system + 知识库 ─────┘                              ↓
                              render_paper（整卷渲染，未调用则兜底补渲染）
                                              ExamResult 交回编排层入库

修订模式（context 携带 revision 指令时）：同一入口、同一全套工具，
跳过意图提取、改用修订提示词，直接在 output/paper.md 上按用户划选
反馈续跑修改，不设渲染兜底（render_paper 由模型按工作流自主调用）。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage

from app.agents.intent import extract_intent
from app.agents.llm import get_chat_model
from app.agents.prompts import load_prompt, render_prompt
from app.agents.schemas import (
    AgentHooks,
    AgentSummary,
    ExamResult,
    RevisionDirective,
)
from app.agents.skills import SkillLoader
from app.agents.tools import (
    PAPER_PATH,
    AgentContext,
    _render_paper_once,
    build_agent_tools,
    finalize,
)
from app.agents.workspace import Workspace, WorkspaceError
from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.storage import StorageError, storage
from app.retrieval.vector_store import PgVectorStore

logger = logging.getLogger(__name__)

_DEFAULT_DURATION_MINUTES = 100

# 工作区以 job 目录为根：材料在 agent/materials/，试卷直写 output/paper.md
_MATERIALS_DIR = "agent/materials"


async def run_exam_agent(
    job_id: UUID,
    context: dict[str, Any],
    hooks: AgentHooks | None = None,
) -> ExamResult:
    """执行试卷 agent（生成或修订），返回结构化结果。

    context 字段（由编排层构造）：
        duration_minutes / need_explanation /
        user_id / school_id / course_id / upload_ids
    预处理阶段应已把用户材料写入工作区 agent/materials/ 目录。

    修订模式：额外携带 revision = {selection, feedback, history}，
    详见 RevisionDirective。
    """
    hooks = hooks or AgentHooks()
    workspace = Workspace(storage, prefix=f"jobs/{job_id}")
    duration = int(context.get("duration_minutes", _DEFAULT_DURATION_MINUTES))

    revision: RevisionDirective | None = None
    if context.get("revision"):
        revision = RevisionDirective.model_validate(context["revision"])

    ctx = AgentContext(
        job_id=job_id,
        workspace=workspace,
        storage=storage,
        skills=SkillLoader(settings.skills_dir),
        vector_store=PgVectorStore(AsyncSessionLocal),
        hooks=hooks,
        user_id=context.get("user_id"),
        school_id=context.get("school_id"),
        course_id=context.get("course_id"),
        upload_ids=list(context.get("upload_ids") or []),
        need_explanation=bool(context.get("need_explanation")),
        duration_minutes=duration,
        render_max_retries=int(
            context.get("max_render_retries", settings.agent_max_render_retries)
        ),
        revision=revision,
    )

    model = get_chat_model()

    if ctx.revision is not None:
        return await _run_revision(ctx, model)

    # ---------- 意图提取 ----------
    intent = await extract_intent(
        model,
        duration_minutes=duration,
        keypoint_list=await _read_optional_material(workspace, f"{_MATERIALS_DIR}/keypoints.md"),
        extra_requirement=await _read_optional_material(
            workspace, f"{_MATERIALS_DIR}/requirements.md"
        ),
    )

    # ---------- 组装 ReAct agent ----------
    material_list = await _material_list(workspace)
    system_prompt = render_prompt(
        load_prompt("system"),
        duration_minutes=str(duration),
        need_explanation_label="需要解析" if ctx.need_explanation else "不需要解析",
        intent=json.dumps(intent.model_dump(), ensure_ascii=False, indent=2),
        material_list=material_list,
        explanation_rule=_explanation_rule(ctx.need_explanation),
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
            {
                "messages": [
                    HumanMessage(
                        content=(
                            "请开始组卷。先阅读材料与技能，规划蓝图后把试卷逐题写入"
                            " output/paper.md，全部完成后渲染 PDF 并输出总结结束。"
                        )
                    )
                ]
            },
            config={"recursion_limit": settings.agent_recursion_limit},
        )
        structured = state.get("structured_response")
        if isinstance(structured, AgentSummary):
            summary_text = structured.summary
    except Exception as exc:
        completed_normally = False
        logger.exception("agent 循环异常中断（job=%s）", job_id)
        await hooks.fire_warning(f"agent 循环中断：{exc}；以已产出内容收尾")

    # 兜底：模型未调用 render_paper 就结束（或循环异常中断）时补渲染一次，保底交付。
    # 单次不重试——模型已结束，无人修正内容，同输入重试必然同失败。
    if ctx.render_status == "not_attempted":
        try:
            await _render_paper_once(ctx)
        except Exception as exc:
            ctx.render_status = "md_only"
            ctx.render_error = str(exc)
            logger.warning("兜底渲染失败（job=%s）: %s", job_id, exc)
            await hooks.fire_warning(f"兜底渲染失败，仅交付 paper.md：{exc}")

    return finalize(
        ctx,
        intent=intent,
        summary=summary_text,
        completed_normally=completed_normally,
    )


async def _run_revision(ctx: AgentContext, model: Any) -> ExamResult:
    """修订分支：携带划选位置、用户反馈与全部历史会话，直接续跑改 output/paper.md。"""
    try:
        paper = await ctx.workspace.read(PAPER_PATH)
    except (WorkspaceError, StorageError) as exc:
        await ctx.hooks.fire_warning(f"试卷不存在（{PAPER_PATH}），无法修订：{exc}")
        return finalize(ctx, intent=None, completed_normally=False)

    history_lines = []
    for r in ctx.revision.history:
        sel_note = f"选中「{_brief(r.selection.text)}」" if r.selection else "（未划选）"
        history_lines.append(f"- 第 {r.round_no} 轮：{sel_note}；反馈：{r.feedback}")
    history_text = "\n".join(history_lines) if history_lines else "（无）"

    sel = ctx.revision.selection
    system_prompt = render_prompt(
        load_prompt("revise_system"),
        paper_content=paper,
        selection_before=sel.before or "（无）",
        selection_text=sel.text,
        selection_after=sel.after or "（无）",
        feedback=ctx.revision.feedback,
        history=history_text,
        material_list=await _material_list(ctx.workspace),
        explanation_rule=_explanation_rule(ctx.need_explanation),
        skill_index=ctx.skills.index_prompt(),
    )

    agent = create_agent(
        model,
        tools=build_agent_tools(ctx),
        system_prompt=system_prompt,
    )

    completed_normally = True
    try:
        await agent.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            "请按用户反馈修订试卷中的所选部分：定位 → edit_file 修改"
                            " output/paper.md → render_paper 重新渲染 → 简短总结并结束。"
                        )
                    )
                ]
            },
            config={"recursion_limit": settings.agent_recursion_limit},
        )
    except Exception as exc:
        completed_normally = False
        logger.exception("修订 agent 循环异常中断（job=%s）", ctx.job_id)
        await ctx.hooks.fire_warning(f"修订循环中断：{exc}；以当前已修改内容收尾")

    return finalize(ctx, intent=None, completed_normally=completed_normally)


def _brief(text: str, limit: int = 50) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def _explanation_rule(need_explanation: bool) -> str:
    if need_explanation:
        return "用户开启了答案解析：每道题必须在《参考答案与解析》中携带对应解析。"
    return "用户未开启答案解析：试卷中不要出现解析段落。"


async def _material_list(workspace: Workspace) -> str:
    materials = await workspace.list_dir(_MATERIALS_DIR)
    if not materials:
        return "（无材料）"
    return "\n".join(f"- {_MATERIALS_DIR}/{name}" for name in materials)


async def _read_optional_material(workspace: Workspace, rel: str) -> str | None:
    """读取可选材料文件，不存在（或读取失败）返回 None。"""
    try:
        return await workspace.read(rel)
    except (WorkspaceError, StorageError) as exc:
        logger.warning("可选材料读取失败，按不存在处理 %s: %s", rel, exc)
        return None

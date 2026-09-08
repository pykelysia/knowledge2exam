"""ReAct agent 的工具集。

6 个工具：todo_write / check_todo / read_file / edit_file / search_knowledge / load_skill。
全部为工厂函数产物：闭包捕获 AgentContext（依赖 + 运行状态），无全局可变状态。

错误处理约定：工具不向循环抛异常，而是返回可读的错误文本——模型据此自我修正；
只有参数不符合 args_schema 的调用由 LangChain 的 ToolNode 统一兜底。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.schemas import (
    AgentHooks,
    ExamIntent,
    ExamQuestion,
    ExamResult,
    TodoItem,
)
from app.agents.skills import SkillError, SkillLoader
from app.agents.workspace import Workspace, WorkspaceError
from app.core.enums import SourceType
from app.retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)

# 题目文件路径：questions/NNN.json
_QUESTION_PATH_RE = re.compile(r"^questions/(\d{3})\.json$")

# 单次 read_file 返回的最大字符数（保护上下文）
MAX_READ_CHARS = 20_000

_KNOWLEDGE_SOURCE_TYPES = (SourceType.book, SourceType.lecture, SourceType.note)


# ---------------------------------------------------------------------------
# 运行上下文：静态依赖 + 运行状态
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """单次 agent 运行的全部依赖与状态。"""

    # 静态依赖
    job_id: UUID
    workspace: Workspace
    skills: SkillLoader
    vector_store: VectorStore
    hooks: AgentHooks
    upload_ids: list[UUID] = field(default_factory=list)
    school_id: UUID | None = None
    course_id: UUID | None = None
    need_explanation: bool = False
    max_retries: int = 3

    # 运行状态
    todos: list[TodoItem] = field(default_factory=list)
    plan_ready_fired: bool = False
    retry_counts: dict[int, int] = field(default_factory=dict)
    abandoned_seqs: set[int] = field(default_factory=set)
    accepted_seqs: set[int] = field(default_factory=set)


async def finalize(
    ctx: AgentContext,
    intent: ExamIntent | None,
    summary: str = "",
    completed_normally: bool = False,
) -> ExamResult:
    """汇总运行产物：读取工作区中的题目文件，结合蓝图与放弃记录生成 ExamResult。"""
    questions: list[ExamQuestion] = []
    for name in await ctx.workspace.list_dir("questions"):
        rel = f"questions/{name}"
        try:
            raw = await ctx.workspace.read(rel)
            questions.append(ExamQuestion.model_validate(json.loads(raw)))
        except Exception as exc:
            logger.warning("跳过无法解析的题目文件 %s: %s", rel, exc)
            await ctx.hooks.fire_warning(f"题目文件 {rel} 无法解析，已跳过：{exc}")
    questions.sort(key=lambda q: q.seq)

    return ExamResult(
        intent=intent,
        plan_items=sorted(ctx.todos, key=lambda t: t.seq),
        questions=questions,
        abandoned_seqs=sorted(ctx.abandoned_seqs),
        summary=summary,
        completed_normally=completed_normally,
    )


# ---------------------------------------------------------------------------
# args schema
# ---------------------------------------------------------------------------


class TodoWriteArgs(BaseModel):
    """todo_write 的参数。"""

    todos: list[TodoItem] = Field(description="完整的蓝图列表；每次调用全量覆盖")


class ReadFileArgs(BaseModel):
    """read_file 的参数。"""

    path: str = Field(description="工作区相对路径，如 materials/past_paper_01.md")


class EditFileArgs(BaseModel):
    """edit_file 的参数。"""

    path: str = Field(description="工作区相对路径")
    old_string: str = Field(
        default="",
        description="要替换的原文；创建新文件时传空字符串（题目文件不受此限，见工具说明）",
    )
    new_string: str = Field(description="新内容或替换后的文本")


class SearchKnowledgeArgs(BaseModel):
    """search_knowledge 的参数。"""

    query: str = Field(description="检索查询语句（知识点/概念描述）")
    top_k: int = Field(default=5, ge=1, le=20, description="返回条数")


class LoadSkillArgs(BaseModel):
    """load_skill 的参数。"""

    name: str = Field(description="技能名称，来自系统提示中的技能索引")


# ---------------------------------------------------------------------------
# 工具工厂
# ---------------------------------------------------------------------------


def _todo_write_tool(ctx: AgentContext) -> StructuredTool:
    async def todo_write(todos: list[TodoItem]) -> str:
        seqs = [t.seq for t in todos]
        if len(seqs) != len(set(seqs)):
            return "错误：todo 中存在重复的 seq，请修正后重新提交。"
        todos = sorted(todos, key=lambda t: t.seq)
        gaps = [
            f"{a}→{b}"
            for a, b in zip(seqs, seqs[1:], strict=False)
            if b - a > 1
        ]
        first = not ctx.plan_ready_fired
        ctx.todos = todos
        ctx.plan_ready_fired = True
        if first:
            await ctx.hooks.fire_plan_ready(todos)

        by_type: dict[str, int] = {}
        done = 0
        for t in todos:
            by_type[t.question_type] = by_type.get(t.question_type, 0) + 1
            if t.status == "completed":
                done += 1
        dist = "、".join(f"{k} {v} 题" for k, v in by_type.items())
        gap_note = f"（注意 seq 不连续：{'、'.join(gaps)}）" if gaps else ""
        return (
            f"蓝图已保存：共 {len(todos)} 题（{dist}）{gap_note}，已完成 {done}/{len(todos)}。"
        )

    return StructuredTool.from_function(
        coroutine=todo_write,
        name="todo_write",
        description=(
            "撰写/更新试卷蓝图（todo 列表）。每条 todo 对应一道题：seq 题号、"
            "question_type(choice/blank/short_answer)、knowledge_point、exam_direction、"
            "difficulty(easy/medium/hard)、status(pending/in_progress/completed)。"
            "首次调用即确定整份试卷的规划；之后每次都提交完整列表（全量覆盖），"
            "用 status 变化来表达推进进度。"
        ),
        args_schema=TodoWriteArgs,
    )


def _check_todo_tool(ctx: AgentContext) -> StructuredTool:
    async def check_todo() -> str:
        if not ctx.todos:
            return "尚未撰写蓝图。请先用 todo_write 规划整份试卷。"
        lines = []
        for t in ctx.todos:
            mark = {"pending": " ", "in_progress": ">", "completed": "x"}[t.status]
            note = f"（{t.knowledge_point}）" if t.status != "completed" else ""
            lines.append(f"[{mark}] {t.seq}. {t.question_type} {note} {t.status}")
        return "\n".join(lines)

    return StructuredTool.from_function(
        coroutine=check_todo,
        name="check_todo",
        description="查看当前蓝图（todo 列表）及各题的完成状态。",
    )


def _read_file_tool(ctx: AgentContext) -> StructuredTool:
    async def read_file(path: str) -> str:
        try:
            return await ctx.workspace.read(path, max_chars=MAX_READ_CHARS)
        except WorkspaceError as exc:
            return f"读取失败：{exc}"

    return StructuredTool.from_function(
        coroutine=read_file,
        name="read_file",
        description="读取工作区中的文件（用户材料、已写题目等），返回文本内容。",
        args_schema=ReadFileArgs,
    )


def _validate_question(
    ctx: AgentContext, question: ExamQuestion
) -> str | None:
    """题目内容校验，返回错误文本或 None。Pydantic 校验之外的业务规则在此。"""
    if ctx.need_explanation:
        if not (question.explanation or "").strip():
            return "本次任务要求提供答案解析，缺少 explanation 字段。"
    return None


def _edit_file_tool(ctx: AgentContext) -> StructuredTool:
    async def edit_file(path: str, old_string: str = "", new_string: str = "") -> str:
        try:
            match = _QUESTION_PATH_RE.match(path)
            if match:
                return await _write_question(ctx, int(match.group(1)), path, new_string)
            result = await ctx.workspace.edit(path, old_string, new_string)
            verb = "已创建" if result.created else "已修改"
            return f"{path} {verb}（{len(result.content)} 字符）。"
        except WorkspaceError as exc:
            return f"编辑失败：{exc}"

    return StructuredTool.from_function(
        coroutine=edit_file,
        name="edit_file",
        description=(
            "编辑工作区文件。一般文件：old_string 传要替换的原文（必须唯一匹配），"
            "创建新文件时 old_string 传空字符串。题目文件 questions/NNN.json："
            "忽略 old_string，new_string 传该题的完整 JSON，工具会校验格式并把"
            "错误信息返回给你以便修正。"
        ),
        args_schema=EditFileArgs,
    )


async def _write_question(ctx: AgentContext, seq: int, path: str, new_string: str) -> str:
    """写入题目文件：解析 → 校验 → 落盘 → 事件。失败时计入重试。"""
    try:
        data = json.loads(new_string)
        question = ExamQuestion.model_validate(data)
    except json.JSONDecodeError as exc:
        error = f"不是合法的 JSON：{exc}"
    except Exception as exc:
        error = str(exc)
    else:
        error = _validate_question(ctx, question)

    if error is not None:
        count = ctx.retry_counts.get(seq, 0) + 1
        ctx.retry_counts[seq] = count
        if count >= ctx.max_retries:
            ctx.abandoned_seqs.add(seq)
            await ctx.hooks.fire_warning(f"第 {seq} 题连续 {count} 次校验失败，已放弃：{error}")
            return (
                f"第 {seq} 题已放弃：连续 {count} 次校验失败（{error}）。"
                "请用 todo_write 将该题标记为「[已放弃]」并继续下一题。"
            )
        return f"第 {seq} 题校验失败（第 {count}/{ctx.max_retries} 次）：{error} 请修正后重试。"

    # 校验通过：成功提交可洗白之前被放弃的状态
    ctx.abandoned_seqs.discard(seq)
    await ctx.workspace.write(path, new_string)
    total = max(len(ctx.todos), len(ctx.accepted_seqs))
    if seq not in ctx.accepted_seqs:
        ctx.accepted_seqs.add(seq)
        total = max(total, len(ctx.accepted_seqs))
        await ctx.hooks.fire_question_accepted(question, len(ctx.accepted_seqs), total)
    return f"第 {seq} 题已保存。进度 {len(ctx.accepted_seqs)}/{total}。"



def _search_knowledge_tool(ctx: AgentContext) -> StructuredTool:
    async def search_knowledge(query: str, top_k: int = 5) -> str:
        filter_expr = _knowledge_filter(ctx)
        if filter_expr is None:
            return "知识库为空：本次任务没有可检索的上传资料，也没有同校同课程的共享内容。"
        try:
            result = await ctx.vector_store.search(
                query=query, filter_expr=filter_expr, top_k=top_k
            )
        except Exception as exc:
            logger.warning("知识库检索失败（job=%s）: %s", ctx.job_id, exc)
            return f"检索失败：{exc}。请换一个查询词重试，或基于已有材料作答。"
        if not result.chunks:
            return "未检索到相关内容。请尝试更换关键词，或基于材料内容作答。"
        blocks = []
        for i, c in enumerate(result.chunks, start=1):
            source = c.get("source_type", "unknown")
            page = c.get("page")
            loc = f"{source} 第 {page} 页" if page else source
            blocks.append(f"[{i}] 来源：{loc}（相似度 {c.get('similarity', 0):.2f}）\n{c['text']}")
        return "\n\n---\n\n".join(blocks)

    return StructuredTool.from_function(
        coroutine=search_knowledge,
        name="search_knowledge",
        description=(
            "检索本次任务可用的知识库（用户上传的书籍/课件/笔记 + 同校同课程的共享内容）。"
            "返回最相关的文本片段。"
        ),
        args_schema=SearchKnowledgeArgs,
    )


def _knowledge_filter(ctx: AgentContext) -> dict[str, Any] | None:
    """构造知识库过滤条件：本 job 上传的叠加类内容 OR 同校同课程共享内容。"""
    branches: list[dict[str, Any]] = []
    for st in _KNOWLEDGE_SOURCE_TYPES:
        if ctx.upload_ids:
            branches.append({
                "and": [
                    {"in": {"upload_id": [str(u) for u in ctx.upload_ids]}},
                    {"eq": {"source_type": st.value}},
                ],
            })
        if ctx.school_id and ctx.course_id:
            branches.append({
                "and": [
                    {"eq": {"school_id": str(ctx.school_id)}},
                    {"eq": {"course_id": str(ctx.course_id)}},
                    {"eq": {"source_type": st.value}},
                    {"eq": {"is_shared": True}},
                ],
            })
    if not branches:
        return None
    return {"or": branches} if len(branches) > 1 else branches[0]


def _load_skill_tool(ctx: AgentContext) -> StructuredTool:
    async def load_skill(name: str) -> str:
        try:
            return ctx.skills.load(name)
        except SkillError as exc:
            return f"技能加载失败：{exc}"

    return StructuredTool.from_function(
        coroutine=load_skill,
        name="load_skill",
        description="加载指定技能的完整内容（技能索引见系统提示）。",
        args_schema=LoadSkillArgs,
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_agent_tools(ctx: AgentContext) -> list[StructuredTool]:
    """构造 ReAct agent 的全部工具。"""
    return [
        _todo_write_tool(ctx),
        _check_todo_tool(ctx),
        _read_file_tool(ctx),
        _edit_file_tool(ctx),
        _search_knowledge_tool(ctx),
        _load_skill_tool(ctx),
    ]

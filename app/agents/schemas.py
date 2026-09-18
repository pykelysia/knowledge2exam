"""Agent 层数据模型。

意图（ExamIntent）、蓝图项（TodoItem）、最终产物（ExamResult）、
修订指令（RevisionDirective）与事件钩子（AgentHooks）。
agent 层通过 AgentHooks 回报进度，不依赖编排层。

试卷采用整卷直写：唯一产物是 output/paper.md，不存在分题的中间结构。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

QUESTION_TYPES = ("choice", "blank", "short_answer")
DIFFICULTIES = ("easy", "medium", "hard")
TODO_STATUSES = ("pending", "in_progress", "completed")

# 修订轮终态
ABANDONED_PREFIX = "[已放弃]"


class ExamIntent(BaseModel):
    """意图提取产物：从用户文本材料中结构化出的考试要求。"""

    exam_scope: str = Field(description="考试范围/课程主题概述")
    question_type_preference: str = Field(
        default="", description="题型占比/题量偏好，材料未提及则为空"
    )
    difficulty_profile: str = Field(default="", description="难度要求概述，未提及则为空")
    focus_points: list[str] = Field(default_factory=list, description="用户要求重点考察的知识点")
    special_requirements: list[str] = Field(
        default_factory=list, description="用户的特殊格式/内容要求"
    )
    summary: str = Field(description="一句话意图总结")


class TodoItem(BaseModel):
    """试卷蓝图项：一条 todo 对应一道题的规划，规划即 todo。"""

    seq: int = Field(ge=1, description="题号，从 1 连续递增")
    question_type: str = Field(description=f"题型：{'/'.join(QUESTION_TYPES)}")
    knowledge_point: str = Field(description="考察的知识点")
    exam_direction: str = Field(default="", description="具体考察方向/设问角度")
    difficulty: str = Field(default="medium", description=f"难度：{'/'.join(DIFFICULTIES)}")
    status: str = Field(default="pending", description=f"状态：{'/'.join(TODO_STATUSES)}")

    @model_validator(mode="after")
    def _check_enums(self) -> TodoItem:
        if self.question_type not in QUESTION_TYPES:
            raise ValueError(
                f"question_type 必须是 {'/'.join(QUESTION_TYPES)}，实际为 {self.question_type!r}"
            )
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(
                f"difficulty 必须是 {'/'.join(DIFFICULTIES)}，实际为 {self.difficulty!r}"
            )
        if self.status not in TODO_STATUSES:
            raise ValueError(f"status 必须是 {'/'.join(TODO_STATUSES)}，实际为 {self.status!r}")
        return self


class AgentSummary(BaseModel):
    """ReAct 循环结束时的结构化总结（response_format）。"""

    total_questions: int = Field(description="已产出的题目总数")
    abandoned_count: int = Field(description="放弃的题目数")
    summary: str = Field(description="一句话总结本次组卷")


class ExamResult(BaseModel):
    """agent 运行结束后的汇总产物，交由编排层入库。

    试卷本体是工作区的 output/paper.md（存储层交付），此处只携带
    蓝图与运行状态，不再有分题结构。
    """

    intent: ExamIntent | None = None
    plan_items: list[TodoItem] = Field(default_factory=list)
    abandoned_seqs: list[int] = Field(default_factory=list)
    summary: str = ""
    completed_normally: bool = Field(
        default=False, description="agent 是否正常走完循环（False = 中断后部分产出）"
    )
    render_status: str = Field(
        default="not_attempted", description="整卷渲染结果：not_attempted/succeeded/md_only"
    )
    render_error: str | None = Field(
        default=None, description="渲染失败原因（含 pandoc stderr 摘要）"
    )


class SelectionAnchor(BaseModel):
    """用户在试卷预览中划选的锚点：选中文本 + 前后文。

    来自渲染后 DOM 的内容锚点（而非 md 源偏移），由 agent 语义定位。
    """

    text: str = Field(min_length=1, description="用户划选的文本")
    before: str = Field(default="", description="选区前文（截取的少量上下文）")
    after: str = Field(default="", description="选区后文（截取的少量上下文）")


class RevisionRound(BaseModel):
    """历史修订轮的摘要，作为续跑会话记录注入提示词。"""

    round_no: int = Field(ge=1, description="轮次号，从 1 递增")
    selection: SelectionAnchor | None = Field(default=None, description="当轮划选锚点")
    feedback: str = Field(description="当轮用户反馈")
    summary: str = Field(default="", description="当轮结果摘要")


class RevisionDirective(BaseModel):
    """修订指令：非 None 时 agent 以修订模式续跑（同一工具集，任务目标不同）。"""

    selection: SelectionAnchor = Field(description="本轮划选的位置与内容")
    feedback: str = Field(min_length=1, description="本轮用户修订要求")
    history: list[RevisionRound] = Field(
        default_factory=list, description="既往修订轮（旧→新），即本 job 的会话记录"
    )


@dataclass
class AgentHooks:
    """事件钩子：编排层注入 async 回调，agent 层不依赖编排层。

    所有钩子可为 None；钩子异常被吞掉并记日志，不影响 agent 主流程。
    """

    on_plan_ready: Callable[[list[TodoItem]], Awaitable[None]] | None = None
    on_progress: Callable[[int, int], Awaitable[None]] | None = None
    on_warning: Callable[[str], Awaitable[None]] | None = None
    on_render_start: Callable[[], Awaitable[None]] | None = None

    async def _safe(self, hook: Callable[..., Awaitable[None]] | None, *args: Any) -> None:
        if hook is None:
            return
        try:
            await hook(*args)
        except Exception:
            logger.exception("agent 钩子执行失败（已忽略）")

    async def fire_plan_ready(self, todos: list[TodoItem]) -> None:
        await self._safe(self.on_plan_ready, todos)

    async def fire_progress(self, completed: int, total: int) -> None:
        await self._safe(self.on_progress, completed, total)

    async def fire_warning(self, message: str) -> None:
        await self._safe(self.on_warning, message)

    async def fire_render_start(self) -> None:
        await self._safe(self.on_render_start)

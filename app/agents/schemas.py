"""Agent 层数据模型。

意图（ExamIntent）、蓝图项（TodoItem）、题目（ExamQuestion）、
最终产物（ExamResult）与事件钩子（AgentHooks）。
agent 层通过 AgentHooks 回调上报进度，不依赖编排层。
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


class ExamQuestion(BaseModel):
    """一道题目的结构化内容（questions/NNN.json 的 schema）。"""

    seq: int = Field(ge=1, description="题号，与蓝图一致")
    question_type: str = Field(description=f"题型：{'/'.join(QUESTION_TYPES)}")
    stem: str = Field(min_length=1, description="题干；填空题空位用 ______ 表示")
    options: dict[str, str] | None = Field(default=None, description="仅选择题：A-D 四个选项内容")
    answer: str = Field(min_length=1, description="正确答案；选择题为 A-D 其一")
    sub_questions: list[str] | None = Field(
        default=None, description="仅简答题：子问题列表，可省略"
    )
    sub_answers: list[str] | None = Field(
        default=None, description="仅简答题：与 sub_questions 按索引一一对应"
    )
    explanation: str | None = Field(default=None, description="答案解析（按用户开关要求提供）")

    @model_validator(mode="after")
    def _check_shape(self) -> ExamQuestion:
        if self.question_type not in QUESTION_TYPES:
            raise ValueError(
                f"question_type 必须是 {'/'.join(QUESTION_TYPES)}，实际为 {self.question_type!r}"
            )

        if self.question_type == "choice":
            missing = [k for k in "ABCD" if not (self.options or {}).get(k)]
            if missing:
                raise ValueError(f"选择题 options 缺少选项：{'、'.join(missing)}")
            extra = set(self.options or {}) - set("ABCD")
            if extra:
                raise ValueError(f"选择题 options 出现多余选项：{'、'.join(sorted(extra))}")
            if self.answer not in "ABCD" or len(self.answer) != 1:
                raise ValueError(f"选择题 answer 必须为 A/B/C/D，实际为 {self.answer!r}")
        elif self.options:
            raise ValueError(f"{self.question_type} 题不应携带 options")

        if self.question_type != "short_answer" and (self.sub_questions or self.sub_answers):
            raise ValueError("只有简答题可以携带 sub_questions/sub_answers")
        if (self.sub_questions is None) != (self.sub_answers is None):
            raise ValueError("sub_questions 与 sub_answers 必须同时提供或同时省略")
        if self.sub_questions and self.sub_answers:
            if len(self.sub_questions) != len(self.sub_answers):
                raise ValueError(
                    f"sub_questions 长度 {len(self.sub_questions)} 与 "
                    f"sub_answers 长度 {len(self.sub_answers)} 不一致"
                )
        return self


class AgentSummary(BaseModel):
    """ReAct 循环结束时的结构化总结（response_format）。"""

    total_questions: int = Field(description="已产出的题目总数")
    abandoned_count: int = Field(description="放弃的题目数")
    summary: str = Field(description="一句话总结本次组卷")


class ExamResult(BaseModel):
    """agent 运行结束后的汇总产物，交由编排层入库。"""

    intent: ExamIntent | None = None
    plan_items: list[TodoItem] = Field(default_factory=list)
    questions: list[ExamQuestion] = Field(default_factory=list)
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


@dataclass
class AgentHooks:
    """事件钩子：编排层注入 async 回调，agent 层不依赖编排层。

    所有钩子可为 None；钩子异常被吞掉并记日志，不影响 agent 主流程。
    """

    on_plan_ready: Callable[[list[TodoItem]], Awaitable[None]] | None = None
    on_question_accepted: Callable[[ExamQuestion, int, int], Awaitable[None]] | None = None
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

    async def fire_question_accepted(self, q: ExamQuestion, completed: int, total: int) -> None:
        await self._safe(self.on_question_accepted, q, completed, total)

    async def fire_warning(self, message: str) -> None:
        await self._safe(self.on_warning, message)

    async def fire_render_start(self) -> None:
        await self._safe(self.on_render_start)

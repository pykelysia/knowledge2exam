"""tests/agents 共享的 fake 模型与钩子记录器。"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.agents.schemas import AgentHooks, ExamQuestion, TodoItem


def tool_call(name: str, args: dict[str, Any], id_: str) -> dict[str, Any]:
    """构造 AIMessage.tool_calls 元素。"""
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}


class FakeToolModel(BaseChatModel):
    """按脚本顺序回放 AIMessage 的假工具调用模型。

    `bound_tool_names` 记录最近一次 bind_tools 收到的工具名（用于断言
    结构化输出工具已被绑定）；`seen_messages` 记录每轮输入消息。
    """

    responses: list[BaseMessage]
    step: int = 0
    bound_tool_names: list[str] = Field(default_factory=list)
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolModel:  # noqa: ANN401
        self.bound_tool_names = sorted(t["name"] if isinstance(t, dict) else t.name for t in tools)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen_messages.append(list(messages))
        idx = min(self.step, len(self.responses) - 1)
        self.step += 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[idx])])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop, run_manager, **kwargs)


class Recorder:
    """记录 AgentHooks 三类事件的容器。"""

    def __init__(self) -> None:
        self.plans: list[list[TodoItem]] = []
        self.questions: list[tuple[ExamQuestion, int, int]] = []
        self.warnings: list[str] = []

    def hooks(self) -> AgentHooks:
        async def on_plan_ready(todos: list[TodoItem]) -> None:
            self.plans.append(list(todos))

        async def on_question_accepted(q: ExamQuestion, completed: int, total: int) -> None:
            self.questions.append((q, completed, total))

        async def on_warning(message: str) -> None:
            self.warnings.append(message)

        return AgentHooks(
            on_plan_ready=on_plan_ready,
            on_question_accepted=on_question_accepted,
            on_warning=on_warning,
        )

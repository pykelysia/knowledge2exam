"""Agent 层抽象接口。

首版 stub：各角色均不调用真实 LLM。真实实现接入 LangGraph（tech-selection.md
第 6 节）后替换。角色划分见 agent-design.md 的「角色划分」表。
"""

from __future__ import annotations

from typing import Any, Protocol


class Agent(Protocol):
    async def run(self, **kwargs: Any) -> Any:  # pragma: no cover
        ...


class StubAgent:
    """通用 stub：返回空结果或透传输入。"""

    role: str = "stub"

    async def run(self, **kwargs: Any) -> Any:
        return kwargs

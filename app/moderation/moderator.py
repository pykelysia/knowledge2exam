"""内容安全检测抽象接口。

首版 stub：空实现，默认全部通过。
后续无侵入接入检测服务，只需替换实现。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModerationResult:
    passed: bool
    provider: str
    labels: list[str] | None = None


class Moderator:
    """内容安全检测抽象接口。"""

    async def check(self, text: str) -> ModerationResult:  # pragma: no cover
        raise NotImplementedError


class StubModerator(Moderator):
    """首版 stub：默认通过。"""

    async def check(self, text: str) -> ModerationResult:
        return ModerationResult(passed=True, provider="stub")

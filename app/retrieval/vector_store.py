"""向量库抽象接口。

首版 stub：不真正接入 pgvector，返回空检索结果。
真实实现见 tech-selection.md 第 3 节（pgvector）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievalResult:
    chunks: list[dict[str, Any]] = field(default_factory=list)


class VectorStore:
    """向量检索抽象接口。"""

    async def search(
        self, query: str, filter_expr: dict[str, Any], top_k: int = 5
    ) -> RetrievalResult:  # pragma: no cover
        raise NotImplementedError


class StubVectorStore(VectorStore):
    """首版 stub：返回空结果。"""

    async def search(
        self, query: str, filter_expr: dict[str, Any], top_k: int = 5
    ) -> RetrievalResult:
        return RetrievalResult()

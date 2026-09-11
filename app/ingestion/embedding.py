"""嵌入客户端：调用 OpenAI 兼容 embeddings 接口。"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.core.debug_log import log_step


class EmbeddingClient:
    """嵌入模型客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str, dimensions: int) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._dimensions = dimensions
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    @classmethod
    def from_settings(cls) -> EmbeddingClient:
        """从配置创建实例。"""
        return cls(
        api_key=(
            getattr(settings, "embedding_api_key", "")
            or getattr(settings, "llm_api_key", "")
        ),
            base_url=getattr(settings, "embedding_base_url", "http://localhost:8000/v1"),
            model=getattr(settings, "embedding_model", "text-embedding-3-small"),
            dimensions=settings.embedding_dimensions,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入文本，返回向量列表。"""
        import time

        if not texts:
            return []

        client = self._get_client()

        # OpenAI embeddings API 支持批量输入
        t0 = time.perf_counter()
        response = await client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self._dimensions,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # 按输入顺序返回向量
        vectors = [item.embedding for item in response.data]

        log_step(
            job_id="",
            name="embedding.embed",
            stage="preprocessing",
            input={"text_count": len(texts), "model": self._model},
            output={"vector_count": len(vectors), "dimensions": len(vectors[0]) if vectors else 0},
            elapsed_ms=elapsed_ms,
        )

        return vectors

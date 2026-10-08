"""向量存储真实实现：pgvector + SQLAlchemy。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.enums import SourceType
from app.ingestion.chunking import Chunk
from app.models.chunk import Chunk as ChunkModel


@dataclass
class RetrievalResult:
    chunks: list[dict[str, Any]] = field(default_factory=list)


class VectorStore:
    """向量检索抽象接口。"""

    async def search(
        self, query: str, filter_expr: dict[str, Any], top_k: int = 5
    ) -> RetrievalResult:  # pragma: no cover
        raise NotImplementedError

    async def upsert(
        self, chunks: list[Chunk], *, session: AsyncSession | None = None
    ) -> None:  # pragma: no cover
        raise NotImplementedError

    async def delete_by_upload(self, upload_id: uuid.UUID) -> None:  # pragma: no cover
        raise NotImplementedError


class StubVectorStore(VectorStore):
    """首版 stub：返回空结果。"""

    async def search(
        self, query: str, filter_expr: dict[str, Any], top_k: int = 5
    ) -> RetrievalResult:
        return RetrievalResult()

    async def upsert(
        self, chunks: list[Chunk], *, session: AsyncSession | None = None
    ) -> None:
        return None

    async def delete_by_upload(self, upload_id: uuid.UUID) -> None:
        return None


class PgVectorStore(VectorStore):
    """pgvector 真实实现。"""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def upsert(
        self, chunks: list[Chunk], *, session: AsyncSession | None = None
    ) -> None:
        """批量写入 chunk 表（幂等：按 upload_id + chunk_index 去重）。

        传入 session 时复用调用方事务（不 commit，由调用方控制提交/回滚），
        保证 chunk 与同事务内未提交的 upload 行对外键可见；
        不传时自开事务并在写入后提交。
        """
        if not chunks:
            return

        if session is not None:
            await self._upsert_chunks(session, chunks)
            return

        async with self._session_factory() as session:
            await self._upsert_chunks(session, chunks)
            await session.commit()

    async def _upsert_chunks(self, session: AsyncSession, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            # 使用 INSERT ... ON CONFLICT 实现幂等
            stmt = insert(ChunkModel).values(
                upload_id=chunk.upload_id,
                source_type=chunk.source_type or SourceType.note,
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                embedding=chunk.embedding,
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["upload_id", "chunk_index"])
            await session.execute(stmt)

    async def search(
        self, query: str, filter_expr: dict[str, Any], top_k: int = 5
    ) -> RetrievalResult:
        """向量相似度搜索 + metadata 过滤。"""
        # 1. 将 query 转为向量
        from app.ingestion.embedding import EmbeddingClient
        embedder = EmbeddingClient.from_settings()

        # 空 query 时使用随机向量（仅用于检索全部）
        if query.strip():
            query_vector = (await embedder.embed([query]))[0]
        else:
            import random
            dim = settings.embedding_dimensions
            query_vector = [random.random() for _ in range(dim)]

        # 2. 构造 SQL WHERE 条件
        where_clauses = self._build_filter(filter_expr)

        # 3. 执行 pgvector 余弦相似度搜索
        async with self._session_factory() as session:
            # 余弦相似度 = 1 - 余弦距离
            distance_col = ChunkModel.embedding.cosine_distance(query_vector).label("distance")
            similarity_col = (1 - distance_col).label("similarity")

            stmt = (
                select(
                    ChunkModel.id,
                    ChunkModel.text,
                    ChunkModel.upload_id,
                    ChunkModel.source_type,
                    ChunkModel.page,
                    ChunkModel.chunk_index,
                    similarity_col,
                )
                .where(and_(*where_clauses))
                .order_by(distance_col)
                .limit(top_k)
            )

            result = await session.execute(stmt)
            rows = result.all()

        chunks = []
        for row in rows:
            source_type_val = row.source_type
            if isinstance(source_type_val, SourceType):
                source_type_val = source_type_val.value
            chunks.append({
                "id": str(row.id),
                "text": row.text,
                "upload_id": str(row.upload_id),
                "source_type": source_type_val,
                "page": row.page,
                "chunk_index": row.chunk_index,
                "similarity": float(row.similarity),
            })

        return RetrievalResult(chunks=chunks)

    async def delete_by_upload(self, upload_id: uuid.UUID) -> None:
        """删除某上传件的所有 chunk。"""
        async with self._session_factory() as session:
            from sqlalchemy import delete
            stmt = delete(ChunkModel).where(ChunkModel.upload_id == upload_id)
            await session.execute(stmt)
            await session.commit()

    def _build_filter(self, expr: dict[str, Any]) -> list:
        """将 filter 表达式转换为 SQLAlchemy WHERE 条件列表。"""
        conditions: list = []

        if not expr:
            return conditions

        # 支持简单的 key=value 过滤
        if "eq" in expr:
            for key, value in expr["eq"].items():
                col = getattr(ChunkModel, key)
                # source_type 是枚举类型，需要转换
                if key == "source_type" and isinstance(value, str):
                    value = SourceType(value)
                conditions.append(col == value)

        if "in" in expr:
            for key, values in expr["in"].items():
                col = getattr(ChunkModel, key)
                # source_type 是枚举类型，需要转换
                if key == "source_type" and values and isinstance(values[0], str):
                    values = [SourceType(v) for v in values]
                conditions.append(col.in_(values))

        if "or" in expr:
            or_conditions = []
            for sub_expr in expr["or"]:
                sub_conditions = self._build_filter(sub_expr)
                if sub_conditions:
                    # 分支内条件必须 AND 成组后作为单个 OR 操作数，
                    # 展平会让分支组合语义退化
                    or_conditions.append(
                        and_(*sub_conditions) if len(sub_conditions) > 1 else sub_conditions[0]
                    )
            if or_conditions:
                conditions.append(or_(*or_conditions))

        if "and" in expr:
            for sub_expr in expr["and"]:
                conditions.extend(self._build_filter(sub_expr))

        return conditions

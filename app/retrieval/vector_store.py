"""向量存储真实实现：pgvector + SQLAlchemy。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

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

    async def upsert(self, chunks: list[Chunk]) -> None:  # pragma: no cover
        raise NotImplementedError

    async def delete_by_resource(self, resource_id: uuid.UUID) -> None:  # pragma: no cover
        raise NotImplementedError


class StubVectorStore(VectorStore):
    """首版 stub：返回空结果。"""

    async def search(
        self, query: str, filter_expr: dict[str, Any], top_k: int = 5
    ) -> RetrievalResult:
        return RetrievalResult()

    async def upsert(self, chunks: list[Chunk]) -> None:
        return None

    async def delete_by_resource(self, resource_id: uuid.UUID) -> None:
        return None


class PgVectorStore(VectorStore):
    """pgvector 真实实现。"""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def upsert(self, chunks: list[Chunk]) -> None:
        """批量写入 chunk 表（幂等：按 resource_id + chunk_index 去重）。"""
        if not chunks:
            return

        async with self._session_factory() as session:
            for chunk in chunks:
                # 使用 INSERT ... ON CONFLICT 实现幂等
                stmt = insert(ChunkModel).values(
                    resource_id=chunk.resource_id,
                    upload_id=chunk.upload_id,
                    user_id=chunk.user_id,
                    school_id=chunk.school_id,
                    course_id=chunk.course_id,
                    source_type=chunk.source_type or SourceType.note,
                    is_shared=chunk.is_shared,
                    page=chunk.page,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    embedding=chunk.embedding,
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["resource_id", "chunk_index"]
                )
                await session.execute(stmt)
            await session.commit()

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
            dim = getattr(settings, "embedding_dimensions", 1536)
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
                    ChunkModel.resource_id,
                    ChunkModel.upload_id,
                    ChunkModel.user_id,
                    ChunkModel.school_id,
                    ChunkModel.course_id,
                    ChunkModel.source_type,
                    ChunkModel.is_shared,
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
                "resource_id": str(row.resource_id),
                "upload_id": str(row.upload_id),
                "user_id": str(row.user_id),
                "school_id": str(row.school_id) if row.school_id else None,
                "course_id": str(row.course_id) if row.course_id else None,
                "source_type": source_type_val,
                "is_shared": row.is_shared,
                "page": row.page,
                "chunk_index": row.chunk_index,
                "similarity": float(row.similarity),
            })

        return RetrievalResult(chunks=chunks)

    async def delete_by_resource(self, resource_id: uuid.UUID) -> None:
        """删除某资源的所有 chunk。"""
        async with self._session_factory() as session:
            from sqlalchemy import delete
            stmt = delete(ChunkModel).where(ChunkModel.resource_id == resource_id)
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
                or_conditions.extend(self._build_filter(sub_expr))
            if or_conditions:
                conditions.append(or_(*or_conditions))

        if "and" in expr:
            for sub_expr in expr["and"]:
                conditions.extend(self._build_filter(sub_expr))

        return conditions

"""向量 chunk 表 `chunk`。

承载向量检索的最小单元，直接挂在 upload 下（1 个 upload 对多个 chunk）。
任务检索按 upload 白名单过滤，不再冗余归属/可见性字段。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.enums import SourceType
from app.models.base import Base, TimestampMixin, uuid_pk

# pgvector 的 Vector 类型
try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None  # type: ignore


class Chunk(Base, TimestampMixin):
    __tablename__ = "chunk"
    __table_args__ = (
        # upsert 幂等去重依赖的复合唯一约束（ON CONFLICT (upload_id, chunk_index)）
        UniqueConstraint("upload_id", "chunk_index"),
        # 向量索引：HNSW + 余弦相似度
        Index(
            "idx_chunk_vector",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"dim": settings.embedding_dimensions},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # 任务白名单过滤 + 按 upload 清理
        Index("idx_chunk_upload", "upload_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    upload_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload.id"), nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # 标题路径（如 "第2章 > 2.3 节"），检索引用用元数据，不参与嵌入
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 自有正文在解析产物中的字符区间（不含重叠前缀），[start, end)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 进入嵌入模型的文本（heading_path + "\n" + text）；存量为 NULL
    embedding_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=False
    )

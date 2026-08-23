"""向量 chunk 表 `chunk`。

承载向量检索的最小单元，metadata 字段同时服务于过滤与断点续跑。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
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
        # 向量索引：HNSW + 余弦相似度
        Index(
            "idx_chunk_vector",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"dim": settings.embedding_dimensions},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # payload 索引：过滤用
        Index(
            "idx_chunk_payload",
            "school_id",
            "course_id",
            "source_type",
            "is_shared",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), nullable=False
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id"), nullable=False
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("school.id"), nullable=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course.id"), nullable=True
    )
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type"), nullable=False
    )
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=False
    )

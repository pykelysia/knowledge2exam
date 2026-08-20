"""资源表 `resource`。

`resource` 与 `upload` 分开：一个上传件可能解析出多个资源，且共享状态属于
解析后的资源而非原始文件。`shared_needs_scope` 约束进入共享库必须有归属。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import SourceType
from app.models.base import Base, TimestampMixin, uuid_pk


class Resource(Base, TimestampMixin):
    __tablename__ = "resource"
    __table_args__ = (
        # 进入共享库必须有归属
        CheckConstraint(
            "is_shared = false OR (school_id IS NOT NULL AND course_id IS NOT NULL)",
            name="shared_needs_scope",
        ),
        # 共享库检索主索引
        Index(
            "idx_resource_shared_scope",
            "school_id",
            "course_id",
            "source_type",
            postgresql_where="is_shared = true",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    upload_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type"), nullable=False
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("school.id"), nullable=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course.id"), nullable=True
    )
    parsed_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

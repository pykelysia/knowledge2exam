"""上传件表 `upload`。

承载 FR-4 的 `manual_not_shareable` 与 `payload_present` 两条 CHECK 约束。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import SourceType
from app.models.base import Base, TimestampMixin, uuid_pk


class Upload(Base, TimestampMixin):
    __tablename__ = "upload"
    __table_args__ = (
        # FR-4：手动输入类内容不可共享，这是数据库约束而非前端约定
        CheckConstraint(
            "source_type NOT IN ('manual_text', 'extra_requirement') OR shareable = false",
            name="manual_not_shareable",
        ),
        # 文件类必须有存储位置，文本类必须有正文
        CheckConstraint(
            "(source_type IN ('manual_text', 'extra_requirement') AND raw_text IS NOT NULL) "
            "OR (source_type NOT IN ('manual_text', 'extra_requirement') "
            "AND storage_key IS NOT NULL)",
            name="payload_present",
        ),
        Index("idx_upload_user", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type"), nullable=False
    )
    # 文件类字段，manual_text / extra_requirement 时为 NULL
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 文本类字段，文件类时为 NULL
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    shareable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parse_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

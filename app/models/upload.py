"""上传件表 `upload`。

文件系统改造后的核心实体：原始文件 + 可见性/标签 + 解析产物登记
（吸收了原 `resource` 表，二者收敛为 1:1）。删除为软删除
（`deleted_at`），个人库中的引用在原件删除后保留并标记失效。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import SourceType, Visibility
from app.models.base import Base, TimestampMixin, uuid_pk


class Upload(Base, TimestampMixin):
    __tablename__ = "upload"
    __table_args__ = (
        # 手动输入类内容不可公开，这是数据库约束而非前端约定
        CheckConstraint(
            "source_type NOT IN ('manual_text', 'extra_requirement') "
            "OR visibility = 'private'",
            name="manual_not_shareable",
        ),
        # 本校可见必须有学校归属
        CheckConstraint(
            "visibility != 'school' OR school_id IS NOT NULL",
            name="school_visibility_needs_school",
        ),
        # 文件类必须有存储位置，文本类必须有正文
        CheckConstraint(
            "(source_type IN ('manual_text', 'extra_requirement') AND raw_text IS NOT NULL) "
            "OR (source_type NOT IN ('manual_text', 'extra_requirement') "
            "AND storage_key IS NOT NULL)",
            name="payload_present",
        ),
        Index("idx_upload_user", "user_id", "created_at"),
        # 公开池检索主索引（仅活跃行）
        Index(
            "idx_upload_pool",
            "visibility",
            "school_id",
            "course_id",
            postgresql_where="deleted_at IS NULL",
        ),
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
    # 可见性与标签（上传时提交；school 可见强制 school_id 非空，见 CHECK）
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, name="visibility"),
        nullable=False,
        default=Visibility.private,
        server_default="private",
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("school.id"), nullable=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course.id"), nullable=True
    )
    # 解析产物登记（上传后异步解析管线写入）
    parsed_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_warnings: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    parse_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 软删除：置位后从库/池消失，个人库引用侧标记「文件已失效」
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

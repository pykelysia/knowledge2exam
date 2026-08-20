"""内容安全记录表 `moderation_record`。首版仅保留结构，检测实现为 stub。"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class ModerationRecord(Base, TimestampMixin):
    __tablename__ = "moderation_record"
    __table_args__ = (Index("ix_moderation_resource", "resource_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), nullable=False
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    labels: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

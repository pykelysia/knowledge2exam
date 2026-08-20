"""refresh token 表 `refresh_token`。

服务端只存 SHA-256 哈希，不落明文（NFR-8）。`family_id` 用于重放检测与整链吊销。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_token"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[AppUser] = relationship(back_populates="refresh_tokens")  # noqa: F821

    __table_args__ = (
        Index("ix_refresh_token_user", "user_id"),
        Index("ix_refresh_token_family", "family_id"),
    )

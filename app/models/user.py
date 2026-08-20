"""用户表 `app_user`。"""

from __future__ import annotations

import uuid

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class AppUser(Base, TimestampMixin):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )

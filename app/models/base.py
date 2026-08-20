"""SQLAlchemy 声明基类与通用字段。

所有 ORM 模型继承 `Base`。ID 统一使用 `Uuid`（PostgreSQL 原生 uuid），
时间戳使用 `TIMESTAMPTZ`。
"""

from __future__ import annotations

import uuid

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator):
    """跨方言的 UUID 类型，绑定 PostgreSQL UUID 并携带 as_uuid=True。"""

    impl = UUID
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        return dialect.type_descriptor(UUID(as_uuid=True))


# 命名约定：供 Alembic 自动生成约束名，保证迁移可复现
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    """主键列：UUID，应用侧生成默认值。"""
    return mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

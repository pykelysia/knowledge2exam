"""学校 `school` 与课程 `course` 表。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, uuid_pk


class School(Base):
    __tablename__ = "school"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    courses: Mapped[list[Course]] = relationship(back_populates="school")  # noqa: F821


class Course(Base):
    __tablename__ = "course"
    __table_args__ = (UniqueConstraint("school_id", "name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    school_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("school.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)

    school: Mapped[School] = relationship(back_populates="courses")  # noqa: F821

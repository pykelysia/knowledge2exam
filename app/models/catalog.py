"""学校 `school` 与课程 `course` 表。

学校与课程是两个互相独立的标签：课程为全局实体（同名课程跨校共用），
文件可只挂课程不挂学校，反之亦然。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk


class School(Base):
    __tablename__ = "school"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)


class Course(Base):
    __tablename__ = "course"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

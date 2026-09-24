"""课程出题偏好表 `course_preference`：按 (school_id, course_id) 作用域沉淀的修订偏好。

用户在修订轮勾选沉淀后，由一次小 LLM 提炼调用把具体反馈泛化为课程级持久
偏好，并与已有条目合并（ADD/UPDATE/DELETE/NOOP，mem0 式去重）。出题与修订
时全量载入该课程条目，聚合为 course-preferences 伪技能注入技能索引（【必读】）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class CoursePreference(Base, TimestampMixin):
    """一条同校同课程的出题偏好（泛化后的规则文本）。"""

    __tablename__ = "course_preference"

    id: Mapped[uuid.UUID] = uuid_pk()
    school_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("school.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 溯源：由哪一轮修订沉淀/最后改写（job 删除级联删修订时保留课程偏好）
    source_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_revision.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )

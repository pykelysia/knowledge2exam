"""规划项表 `plan_item`。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class PlanItem(Base, TimestampMixin):
    __tablename__ = "plan_item"
    __table_args__ = (UniqueConstraint("job_id", "seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_point: Mapped[str] = mapped_column(Text, nullable=False)
    exam_direction: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    reference_source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_item.id"), nullable=True
    )

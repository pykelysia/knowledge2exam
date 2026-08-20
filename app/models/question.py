"""题目表 `question` 与重试日志 `retry_log`。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Question(Base, TimestampMixin):
    __tablename__ = "question"
    __table_args__ = (
        UniqueConstraint("job_id", "seq"),
        # 选择题必须有四个选项
        CheckConstraint(
            "question_type <> 'choice' OR options IS NOT NULL", name="choice_has_options"
        ),
        # 子问题与子答案必须同时存在或同时为空
        CheckConstraint(
            "(sub_questions IS NULL) = (sub_answers IS NULL)", name="sub_pair"
        ),
        CheckConstraint("retry_count <= 3", name="retry_bounded"),
        Index("idx_question_job", "job_id", "seq"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    plan_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_item.id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(Text, nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sub_questions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sub_answers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replanned_from: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_item.id"), nullable=True
    )
    review_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")


class RetryLog(Base):
    __tablename__ = "retry_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("question.id", ondelete="CASCADE"), nullable=True
    )
    plan_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_item.id"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    counted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

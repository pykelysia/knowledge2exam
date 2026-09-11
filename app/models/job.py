"""任务相关表：`job`、`job_upload`、`job_stage`。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Job(Base, TimestampMixin):
    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint("duration_minutes BETWEEN 5 AND 300", name="valid_duration"),
        Index("idx_job_user", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("school.id"), nullable=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    need_explanation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    planned_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    md_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    finished_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class JobUpload(Base):
    __tablename__ = "job_upload"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), primary_key=True
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload.id"), primary_key=True
    )


class JobStage(Base):
    __tablename__ = "job_stage"
    __table_args__ = (
        UniqueConstraint("job_id", "seq"),
        Index("idx_job_stage_job_seq", "job_id", "seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

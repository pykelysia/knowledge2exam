"""试卷修订轮次表 `paper_revision`：job 级的修订会话记录。

每轮记录用户的划选锚点与反馈，并在开跑前/结束后各留存一份试卷全文快照，
天然提供回溯能力；历史轮次会注入修订提示词，构成 agent 的会话记忆。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class PaperRevision(Base, TimestampMixin):
    """一次划选反馈修订轮。"""

    __tablename__ = "paper_revision"
    __table_args__ = (UniqueConstraint("job_id", "round_no"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # running / done / failed
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    selection: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_before: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snapshot_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

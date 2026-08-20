"""LLM 调用记录表 `llm_call`（NFR-4 可观测性）。"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class LlmCall(Base, TimestampMixin):
    __tablename__ = "llm_call"
    __table_args__ = (Index("idx_llm_call_job", "job_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    agent_role: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

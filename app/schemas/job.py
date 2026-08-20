"""任务相关 schema。"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    pending = "pending"
    preprocessing = "preprocessing"
    planning = "planning"
    generating = "generating"
    reviewing = "reviewing"
    rendering = "rendering"
    completed = "completed"
    partially_completed = "partially_completed"
    failed = "failed"
    cancelled = "cancelled"


class Stage(StrEnum):
    preprocessing = "preprocessing"
    planning = "planning"
    generating = "generating"
    reviewing = "reviewing"
    rendering = "rendering"


class JobCreate(BaseModel):
    upload_ids: list[uuid.UUID] = Field(default_factory=list)
    school_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    duration_minutes: int = Field(default=100, ge=5, le=300)
    need_explanation: bool = False
    enable_review: bool = False


class JobAccepted(BaseModel):
    job_id: uuid.UUID
    status: JobStatus = JobStatus.pending
    created_at: datetime


class Plan(BaseModel):
    total: int
    distribution: dict[str, int] = Field(default_factory=dict)


class Progress(BaseModel):
    completed: int = 0
    total: int = 0
    retried: int = 0
    replanned: int = 0
    abandoned: int = 0


class Warning(BaseModel):
    code: str
    upload_id: uuid.UUID | None = None
    message: str


class Artifacts(BaseModel):
    md_url: str | None = None
    pdf_url: str | None = None


class Job(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    stage: Stage | None = None
    duration_minutes: int = 100
    need_explanation: bool = False
    enable_review: bool = False
    plan: Plan | None = None
    progress: Progress | None = None
    warnings: list[Warning] = Field(default_factory=list)
    artifacts: Artifacts | None = None
    last_event_seq: int = 0
    error_code: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

"""试卷修订相关 schema。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SelectionPayload(BaseModel):
    """前端划选锚点：选中文本 + 前后文（内容定位，不用偏移）。"""

    text: str = Field(min_length=1, description="用户划选的文本")
    before: str = Field(default="", description="选区前文（少量上下文）")
    after: str = Field(default="", description="选区后文（少量上下文）")


class RevisionCreate(BaseModel):
    """提交一轮修订：划选位置与内容 + 用户要求。"""

    selection: SelectionPayload
    feedback: str = Field(min_length=1, description="用户对所选部分的修订要求")


class RevisionAccepted(BaseModel):
    """修订受理回执。"""

    revision_id: uuid.UUID
    round_no: int
    status: str = "running"


class RevisionItem(BaseModel):
    """修订历史条目（会话记录）。"""

    revision_id: uuid.UUID
    round_no: int
    status: str
    selection: SelectionPayload | None = None
    feedback: str
    summary: str | None = None
    error: str | None = None
    created_at: datetime
    applied_at: datetime | None = None


class RevisionsResponse(BaseModel):
    job_id: uuid.UUID
    total: int = 0
    revisions: list[RevisionItem] = Field(default_factory=list)

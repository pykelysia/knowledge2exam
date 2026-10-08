"""公开池相关 schema。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.core.enums import SourceType, Visibility
from app.schemas.upload import FileOwner


class PoolFile(BaseModel):
    """公开池条目（本校 + 全局可见，排除本人上传）。"""

    upload_id: uuid.UUID
    source_type: SourceType
    filename: str | None = None
    size_bytes: int | None = None
    visibility: Visibility
    school_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    school_name: str | None = None
    course_name: str | None = None
    char_count: int | None = None
    created_at: datetime
    owner: FileOwner
    # 是否已保存到个人库
    is_saved: bool = False
    # 内容语义检索得分（仅当 q 命中语义检索时存在）
    semantic_score: float | None = None


class PoolFilesResponse(BaseModel):
    files: list[PoolFile]
    total: int
    page: int
    page_size: int


class ImportAccepted(BaseModel):
    saved: bool = True
    upload_id: uuid.UUID

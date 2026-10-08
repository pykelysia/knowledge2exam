"""上传件与个人库相关 schema。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import TEXT_SOURCE_TYPES, SourceType, Visibility

# 全局文本兜底上限；更细的按类型限制（max_extra_requirement_chars 等）由端点执行
_RAW_TEXT_MAX_CHARS = 200_000


class TextUploadCreate(BaseModel):
    source_type: SourceType
    raw_text: str = Field(min_length=1, max_length=_RAW_TEXT_MAX_CHARS)

    def model_post_init(self, __context) -> None:  # noqa: ANN001
        if self.source_type not in TEXT_SOURCE_TYPES:
            raise ValueError("source_type 仅限 manual_text / extra_requirement")


class Upload(BaseModel):
    """上传件登记结果。"""

    upload_id: uuid.UUID
    source_type: SourceType
    filename: str | None = None
    size_bytes: int | None = None
    visibility: Visibility = Visibility.private
    parse_status: str = "pending"


class UploadUpdate(BaseModel):
    """修改可见性 / 标签（未提供的字段保持不变，显式 null 表示清除标签）。"""

    visibility: Visibility | None = None
    school_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None


class FileOwner(BaseModel):
    user_id: uuid.UUID
    username: str


class LibraryFile(BaseModel):
    """个人库条目：我的上传或从公开池保存的引用。"""

    upload_id: uuid.UUID
    source_type: SourceType
    filename: str | None = None
    size_bytes: int | None = None
    visibility: Visibility
    school_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    char_count: int | None = None
    parse_status: str
    parse_error: str | None = None
    parse_warnings: list[dict] = Field(default_factory=list)
    created_at: datetime
    owner: FileOwner
    # 是否为公开池保存的引用（false = 本人上传）
    imported: bool = False
    imported_at: datetime | None = None
    # available | parsing | failed | deleted（deleted = 原件已删除，文件已失效）
    status: str


class LibraryResponse(BaseModel):
    files: list[LibraryFile]


class UploadPreview(BaseModel):
    """文件预览：基于解析产物文本。"""

    upload_id: uuid.UUID
    filename: str | None = None
    source_type: SourceType
    visibility: Visibility
    school_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    school_name: str | None = None
    course_name: str | None = None
    char_count: int | None = None
    text: str
    truncated: bool = False

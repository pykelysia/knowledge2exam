"""上传件相关 schema。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.enums import TEXT_SOURCE_TYPES, SourceType

# 全局文本兜底上限；更细的按类型限制（max_extra_requirement_chars 等）由端点执行
_RAW_TEXT_MAX_CHARS = 200_000


class TextUploadCreate(BaseModel):
    source_type: SourceType
    raw_text: str = Field(min_length=1, max_length=_RAW_TEXT_MAX_CHARS)

    def model_post_init(self, __context) -> None:  # noqa: ANN001
        if self.source_type not in TEXT_SOURCE_TYPES:
            raise ValueError("source_type 仅限 manual_text / extra_requirement")


class Upload(BaseModel):
    upload_id: uuid.UUID
    source_type: SourceType
    filename: str | None = None
    size_bytes: int | None = None
    shareable: bool = False

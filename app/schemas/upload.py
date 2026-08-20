"""上传件相关 schema。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.core.enums import TEXT_SOURCE_TYPES, SourceType


class TextUploadCreate(BaseModel):
    source_type: SourceType
    raw_text: str

    def model_post_init(self, __context) -> None:  # noqa: ANN001
        if self.source_type not in TEXT_SOURCE_TYPES:
            raise ValueError("source_type 仅限 manual_text / extra_requirement")


class Preview(BaseModel):
    char_count: int | None = None
    page_count: int | None = None
    excerpt: str | None = None


class Upload(BaseModel):
    upload_id: uuid.UUID
    source_type: SourceType
    filename: str | None = None
    size_bytes: int | None = None
    shareable: bool = False
    parse_status: str
    parse_error: str | None = None
    preview: Preview | None = None

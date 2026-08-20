"""公共 schema：统一错误响应与错误码。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.core.exceptions import ErrorCode


class ErrorResponse(BaseModel):
    error_code: ErrorCode
    message: str
    detail: dict[str, Any] | None = None

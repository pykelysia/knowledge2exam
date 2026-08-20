"""统一错误体系。

`ErrorCode` 全集见 [api.md](../../docs/api.md#11-错误码) 第 11 节，包含仅作为
warning / error 事件出现、无 HTTP 状态的码（如 `PARSE_FAILED`、`PLANNING_FAILED`）。
有 HTTP 状态的码在此映射到对应状态码，供全局异常处理器转换为统一 `ErrorResponse`。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # 输入与格式
    INPUT_EMPTY = "INPUT_EMPTY"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    SHARE_NOT_ALLOWED = "SHARE_NOT_ALLOWED"
    SHARE_SCOPE_REQUIRED = "SHARE_SCOPE_REQUIRED"
    INVALID_DURATION = "INVALID_DURATION"
    # 资源与任务
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    UPLOAD_IN_USE = "UPLOAD_IN_USE"
    JOB_NOT_READY = "JOB_NOT_READY"
    JOB_ALREADY_FINISHED = "JOB_ALREADY_FINISHED"
    # 生成过程（无 HTTP 状态，作为 warning / error 事件）
    PARSE_FAILED = "PARSE_FAILED"
    MODERATION_REJECTED = "MODERATION_REJECTED"
    PLANNING_FAILED = "PLANNING_FAILED"
    GENERATION_EXHAUSTED = "GENERATION_EXHAUSTED"
    RENDER_FAILED = "RENDER_FAILED"
    # 服务
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    # 鉴权
    USER_EXISTS = "USER_EXISTS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_MISSING = "TOKEN_MISSING"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    REFRESH_INVALID = "REFRESH_INVALID"
    REFRESH_REUSE_DETECTED = "REFRESH_REUSE_DETECTED"


# 有 HTTP 状态码的错误码映射。不在映射中的错误码不作为请求失败返回（只出现在
# warning / error 事件中），若误用会走 500 兜底。
_ERROR_CODE_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INPUT_EMPTY: 400,
    ErrorCode.UNSUPPORTED_FORMAT: 400,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.PDF_ENCRYPTED: 400,
    ErrorCode.SHARE_NOT_ALLOWED: 400,
    ErrorCode.SHARE_SCOPE_REQUIRED: 400,
    ErrorCode.INVALID_DURATION: 400,
    ErrorCode.JOB_NOT_FOUND: 404,
    ErrorCode.UPLOAD_IN_USE: 409,
    ErrorCode.JOB_NOT_READY: 409,
    ErrorCode.JOB_ALREADY_FINISHED: 409,
    ErrorCode.RENDER_FAILED: 409,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.USER_EXISTS: 409,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.TOKEN_MISSING: 401,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.REFRESH_INVALID: 401,
    ErrorCode.REFRESH_REUSE_DETECTED: 401,
}


class AppException(Exception):
    """业务异常：携带 `error_code`、`message` 与可选 `detail`。

    全局异常处理器据此转换为统一错误响应 `{error_code, message, detail}`。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.message = message or code.value
        self.detail = detail
        self.status_code = status_code or _ERROR_CODE_HTTP_STATUS.get(code, 500)
        super().__init__(self.message)


# ---- 便捷构造 ----
# 鉴权相关


class TokenMissing(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.TOKEN_MISSING, "未携带 access token")


class TokenExpired(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.TOKEN_EXPIRED, "access token 已过期")


class InvalidCredentials(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.INVALID_CREDENTIALS, "邮箱/用户名或密码错误")


class RefreshInvalid(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.REFRESH_INVALID, "refresh token 已吊销或过期")


class RefreshReuseDetected(AppException):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.REFRESH_REUSE_DETECTED,
            "检测到已吊销 refresh token 被复用，整条链已吊销",
        )


class UserExists(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.USER_EXISTS, "邮箱或用户名已被注册")


# ---- 资源与任务 ----


class JobNotFound(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.JOB_NOT_FOUND, "任务不存在或无权访问")


class UploadNotFound(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.JOB_NOT_FOUND, "上传件不存在或无权访问")


class UploadInUse(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.UPLOAD_IN_USE, "上传件已被任务引用，不可删除")


class JobNotReady(AppException):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(ErrorCode.JOB_NOT_READY, message or "产物尚未生成")


class JobAlreadyFinished(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.JOB_ALREADY_FINISHED, "任务已终结，不可取消")


class RenderFailed(AppException):
    def __init__(self) -> None:
        super().__init__(ErrorCode.RENDER_FAILED, "PDF 渲染失败，md 仍可下载")

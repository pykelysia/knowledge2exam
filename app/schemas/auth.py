"""鉴权相关请求 / 响应 schema。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field, model_validator

# bcrypt 算法对超过 72 字节的密码直接抛错，在入口拦截（注意按字节而非字符计）
_BCRYPT_MAX_PASSWORD_BYTES = 72


def _validate_password_bytes(password: str) -> str:
    if len(password.encode("utf-8")) > _BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"密码长度不能超过 {_BCRYPT_MAX_PASSWORD_BYTES} 字节")
    return password


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8)

    @model_validator(mode="after")
    def _check_password(self) -> RegisterRequest:
        _validate_password_bytes(self.password)
        return self


class LoginRequest(BaseModel):
    email: EmailStr | None = None
    username: str | None = None
    password: str

    @model_validator(mode="after")
    def _check_shape(self) -> LoginRequest:
        # 至少提供 email 或 username 之一（优先 email）
        if self.email is None and self.username is None:
            raise ValueError("email 与 username 至少提供一个")
        _validate_password_bytes(self.password)
        return self


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class User(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    username: str


class UserResponse(BaseModel):
    user: User


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user: User | None = None

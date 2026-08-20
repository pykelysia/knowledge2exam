"""鉴权相关请求 / 响应 schema。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr | None = None
    username: str | None = None
    password: str

    def model_post_init(self, __context) -> None:  # noqa: ANN001
        # 至少提供 email 或 username 之一（优先 email）
        if self.email is None and self.username is None:
            raise ValueError("email 与 username 至少提供一个")


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

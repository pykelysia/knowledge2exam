"""双 JWT 安全原语。

- access token：JWT（HS256），15 分钟，随 `Authorization: Bearer` 携带。
- refresh token：不透明随机串，30 天，服务端只存 SHA-256 哈希，每次刷新轮换，
  同 `family_id` 串成链，用于重放检测（NFR-8）。
- 密码：bcrypt 哈希。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings

# ---- 密码 ----


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# ---- access token ----


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """签发 access token，返回 (token, expires_in_seconds)。"""
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> uuid.UUID:
    """校验并解析 access token，返回 user_id。

    失败时抛出 `jwt.ExpiredSignatureError` / `jwt.InvalidTokenError`，由调用方
    转换为对应的 `AppException`（TOKEN_EXPIRED / TOKEN_MISSING）。
    """
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return uuid.UUID(payload["sub"])


# ---- refresh token ----


def generate_refresh_token() -> str:
    """生成不透明 refresh token（明文，仅返回给客户端一次）。"""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """refresh token 的 SHA-256 哈希（落库，不存明文）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)

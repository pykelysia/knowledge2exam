"""鉴权依赖注入。"""

from __future__ import annotations

import uuid

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.exceptions import TokenExpired, TokenMissing
from app.core.security import decode_access_token
from app.models.user import AppUser

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> AppUser:
    """从 `Authorization: Bearer` 解析出当前用户。"""
    if credentials is None or not credentials.credentials:
        raise TokenMissing()

    try:
        user_id: uuid.UUID = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired() from exc
    except jwt.InvalidTokenError as exc:
        raise TokenMissing() from exc

    user = await db.get(AppUser, user_id)
    if user is None:
        # token 有效但用户已不存在（被删除）
        raise TokenMissing()
    return user


async def get_current_user_id(
    user: AppUser = Depends(get_current_user),
) -> uuid.UUID:
    """便捷依赖：直接返回 user_id。"""
    return user.id

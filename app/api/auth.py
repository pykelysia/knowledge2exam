"""鉴权端点：注册 / 登录 / 刷新 / 登出。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_db
from app.core.exceptions import (
    InvalidCredentials,
    RefreshInvalid,
    RefreshReuseDetected,
    UserExists,
)
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expires_at,
    verify_password,
)
from app.models.auth import RefreshToken
from app.models.user import AppUser
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    User,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


def _user_to_schema(user: AppUser) -> User:
    return User(user_id=user.id, email=user.email, username=user.username)


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """在响应上设置 httpOnly Cookie。"""
    response.set_cookie(
        "access_token",
        access_token,
        max_age=settings.cookie_access_token_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        max_age=settings.cookie_refresh_token_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    """清除认证 Cookie。"""
    response.delete_cookie("access_token", path="/", domain=settings.cookie_domain)
    response.delete_cookie("refresh_token", path="/", domain=settings.cookie_domain)


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> UserResponse:
    existing = await db.scalar(
        select(AppUser).where(
            (AppUser.email == payload.email) | (AppUser.username == payload.username)
        )
    )
    if existing is not None:
        raise UserExists()

    user = AppUser(
        email=payload.email,
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse(user=_user_to_schema(user))


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)
) -> UserResponse:
    stmt = select(AppUser)
    if payload.email is not None:
        stmt = stmt.where(AppUser.email == payload.email)
    else:
        stmt = stmt.where(AppUser.username == payload.username)

    user = await db.scalar(stmt)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise InvalidCredentials()

    user_schema = await _issue_tokens(db, response, user)
    return UserResponse(user=user_schema)


@router.post("/refresh", response_model=UserResponse)
async def refresh(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> UserResponse:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise RefreshInvalid()

    token_hash = hash_refresh_token(refresh_token)
    row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    if row is None or row.expires_at < datetime.now(UTC):
        raise RefreshInvalid()

    if row.revoked_at is not None:
        # 已吊销的 token 再次使用：泄露重放，吊销整条链
        await _revoke_family(db, row.family_id)
        raise RefreshReuseDetected()

    user = await db.get(AppUser, row.user_id)
    if user is None:
        raise RefreshInvalid()

    family_id = row.family_id

    # 轮换：旧 token 立即吊销，签发同 family_id 的新 token
    row.revoked_at = datetime.now(UTC)
    await db.flush()

    user_schema = await _issue_tokens(db, response, user, family_id=family_id)
    return UserResponse(user=user_schema)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> Response:
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        token_hash = hash_refresh_token(refresh_token)
        row = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if row is not None:
            if row.revoked_at is not None:
                # 已吊销的 token 再次使用：判为泄露重放，吊销整条链
                await _revoke_family(db, row.family_id)
                raise RefreshReuseDetected()
            row.revoked_at = datetime.now(UTC)
            await db.commit()

    _clear_auth_cookies(response)
    return Response(status_code=204)


async def _issue_tokens(
    db: AsyncSession,
    response: Response,
    user: AppUser,
    family_id: uuid.UUID | None = None,
) -> User:
    """签发 access/refresh token 并写入 Cookie。"""
    access_token, _ = create_access_token(user.id)
    refresh_token = generate_refresh_token()

    if family_id is None:
        family_id = uuid.uuid4()

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            family_id=family_id,
            expires_at=refresh_token_expires_at(),
        )
    )
    await db.commit()

    _set_auth_cookies(response, access_token, refresh_token)
    return _user_to_schema(user)


async def _revoke_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    """吊销整条刷新链（同 family_id 的全部 token）。"""
    from sqlalchemy import update

    now = datetime.now(UTC)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()

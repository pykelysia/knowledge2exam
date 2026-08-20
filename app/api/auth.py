"""鉴权端点：注册 / 登录 / 刷新 / 登出。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    AuthTokens,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    User,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


def _user_to_schema(user: AppUser) -> User:
    return User(user_id=user.id, email=user.email, username=user.username)


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


@router.post("/login", response_model=AuthTokens)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> AuthTokens:
    stmt = select(AppUser)
    if payload.email is not None:
        stmt = stmt.where(AppUser.email == payload.email)
    else:
        stmt = stmt.where(AppUser.username == payload.username)

    user = await db.scalar(stmt)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise InvalidCredentials()

    return await _issue_tokens(db, user)


@router.post("/refresh", response_model=AuthTokens)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> AuthTokens:
    token_hash = hash_refresh_token(payload.refresh_token)
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

    access_token, expires_in = create_access_token(user.id)
    refresh_token = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            family_id=family_id,
            expires_at=refresh_token_expires_at(),
        )
    )
    await db.commit()

    return AuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/logout", status_code=204)
async def logout(payload: LogoutRequest, db: AsyncSession = Depends(get_db)) -> Response:
    token_hash = hash_refresh_token(payload.refresh_token)
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
    return Response(status_code=204)


async def _issue_tokens(db: AsyncSession, user: AppUser) -> AuthTokens:
    access_token, expires_in = create_access_token(user.id)
    refresh_token = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            family_id=uuid.uuid4(),
            expires_at=refresh_token_expires_at(),
        )
    )
    await db.commit()
    return AuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        user=_user_to_schema(user),
    )


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

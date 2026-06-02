from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_dep, get_current_user_id, redis_dep
from app.common.config import settings
from app.common.errors import envelope
from app.common.rate_limit import ensure_rate_limit
from app.common.security import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.domain.preferences.markdown_profile import ensure_user_preference_file
from app.infra.models.user import User

router = APIRouter()


class RegisterRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    password: str
    nickname: str | None = None


class LoginRequest(BaseModel):
    account: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


async def _issue_tokens(user_id: str, redis_client: Any) -> TokenResponse:
    access_token, _ = create_access_token(user_id)
    refresh_token, refresh_jti = create_refresh_token(user_id)
    key = f"rt:{refresh_jti}"
    await redis_client.setex(key, settings.REFRESH_TOKEN_TTL_SECONDS, user_id)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/register")
async def register(payload: RegisterRequest, request: Request, db: db_dep, redis: redis_dep):
    if not payload.email and not payload.phone:
        raise HTTPException(status_code=400, detail="email or phone required")

    conditions = []
    if payload.email:
        conditions.append(User.email == payload.email)
    if payload.phone:
        conditions.append(User.phone == payload.phone)
    stmt = select(User).where(or_(*conditions))
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="email or phone already exists")

    user_id = str(uuid4())
    nickname = payload.nickname or payload.email or payload.phone or "user"
    user = User(
        id=user_id,
        email=payload.email,
        phone=payload.phone,
        nickname=nickname,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await ensure_user_preference_file(user_id)
    await db.commit()

    tokens = await _issue_tokens(user_id, redis)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(tokens.model_dump(), trace_id)


@router.post("/login")
async def login(payload: LoginRequest, request: Request, db: db_dep, redis: redis_dep):
    client_ip = request.client.host if request.client else "unknown"
    await ensure_rate_limit(
        redis,
        key=f"rl:login:{client_ip}:{payload.account}",
        limit=10,
        window_seconds=60,
    )
    if "@" in payload.account:
        stmt = select(User).where(User.email == payload.account)
    else:
        stmt = select(User).where(User.phone == payload.account)

    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    tokens = await _issue_tokens(user.id, redis)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(tokens.model_dump(), trace_id)


@router.post("/token/refresh")
async def refresh_token(payload: RefreshRequest, request: Request, redis: redis_dep):
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message) from exc

    jti = claims.get("jti")
    user_id = str(claims.get("sub"))
    if not jti or not user_id:
        raise HTTPException(status_code=401, detail="refresh token invalid")
    key = f"rt:{jti}"
    stored_user = await redis.get(key)
    if stored_user != user_id:
        raise HTTPException(status_code=401, detail="refresh token revoked")

    await redis.delete(key)
    tokens = await _issue_tokens(user_id, redis)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(tokens.model_dump(), trace_id)


@router.post("/logout")
async def logout(payload: LogoutRequest, request: Request, redis: redis_dep):
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
        jti = claims.get("jti")
        if jti:
            await redis.delete(f"rt:{jti}")
    except AuthError:
        pass

    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"logged_out": True}, trace_id)


@router.post("/password/change")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"updated": True}, trace_id)

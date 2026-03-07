from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.common.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class AuthError(Exception):
    message: str


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode_token(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def create_access_token(user_id: str) -> tuple[str, str]:
    jti = str(uuid4())
    now = _now()
    payload = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS),
        "jti": jti,
    }
    return _encode_token(payload), jti


def create_refresh_token(
    user_id: str,
    *,
    session_id: str | None = None,
    family_id: str | None = None,
    rotation: int | None = None,
) -> tuple[str, str]:
    jti = str(uuid4())
    now = _now()
    payload = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(seconds=settings.REFRESH_TOKEN_TTL_SECONDS),
        "jti": jti,
    }
    if session_id:
        payload["sid"] = session_id
    if family_id:
        payload["fid"] = family_id
    if rotation is not None:
        payload["rot"] = int(rotation)
    return _encode_token(payload), jti


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALG],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except JWTError as exc:
        raise AuthError("invalid token") from exc

    token_type = payload.get("type")
    if expected_type and token_type != expected_type:
        raise AuthError("invalid token type")
    return payload

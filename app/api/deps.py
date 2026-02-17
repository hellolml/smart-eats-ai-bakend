from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as redis

from app.common.security import AuthError, decode_token
from app.domain.app.schemas import RestaurantsQuery
from app.infra.minio import MinioStub, get_minio
from app.infra.db import get_db
from app.infra.redis import get_redis


db_dep = Annotated[AsyncSession, Depends(get_db)]
redis_dep = Annotated[redis.Redis, Depends(get_redis)]
minio_dep = Annotated[MinioStub, Depends(get_minio)]


def _get_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth.split(" ", 1)[1].strip()


async def get_current_user_id(request: Request) -> str:
    token = _get_bearer_token(request)
    try:
        payload = decode_token(token, expected_type="access")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message) from exc
    return str(payload.get("sub"))


async def get_optional_user_id(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth:
        return None
    token = _get_bearer_token(request)
    try:
        payload = decode_token(token, expected_type="access")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message) from exc
    return str(payload.get("sub"))


async def parse_restaurants_query(
    q: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    lat: float | None = Query(default=None),
    lng: float | None = Query(default=None),
) -> RestaurantsQuery:
    try:
        return RestaurantsQuery(q=q, sort=sort, tag=tag, lat=lat, lng=lng)
    except ValidationError as exc:
        first_error = exc.errors()[0].get("msg") if exc.errors() else "筛选参数不合法"
        raise HTTPException(
            status_code=422,
            detail=f"筛选参数不合法：{first_error}",
        ) from exc

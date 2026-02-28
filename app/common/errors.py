from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

INVALID_PARAMS = 40001
AUTH_REQUIRED = 40101
AUTH_INVALID = 40102
FORBIDDEN = 40301
NOT_FOUND = 40401
RATE_LIMITED = 42901
INTERNAL_ERROR = 50001
REDIS_UNAVAILABLE = 50301
LLM_UPSTREAM_ERROR = 52001
THIRD_PARTY_ERROR = 53001


@dataclass
class AppError(Exception):
    code: int
    message: str
    http_status: int = 400


def app_error_from_http(exc: HTTPException) -> AppError:
    if exc.status_code == 401:
        return AppError(code=AUTH_REQUIRED, message=str(exc.detail), http_status=401)
    if exc.status_code == 403:
        return AppError(code=FORBIDDEN, message=str(exc.detail), http_status=403)
    if exc.status_code == 404:
        return AppError(code=NOT_FOUND, message=str(exc.detail), http_status=404)
    if exc.status_code == 429:
        return AppError(code=RATE_LIMITED, message=str(exc.detail), http_status=429)
    if exc.status_code >= 500:
        return AppError(code=INTERNAL_ERROR, message=str(exc.detail), http_status=exc.status_code)
    return AppError(code=INVALID_PARAMS, message=str(exc.detail), http_status=exc.status_code)


def envelope(data: Any, trace_id: str, code: int = 0, message: str = "ok") -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "trace_id": trace_id}

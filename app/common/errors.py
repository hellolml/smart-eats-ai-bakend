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

GROUP_DECISION_NOT_FOUND = 44001
GROUP_DECISION_ALREADY_CLOSED = 44002
GROUP_DECISION_VOTE_ITEM_NOT_FOUND = 44003
GROUP_DECISION_ONLY_CREATOR_CAN_CLOSE = 44004
GROUP_DECISION_INVALID_SHARE_TOKEN = 44005
GROUP_DECISION_LINK_EXPIRED = 44006
GROUP_DECISION_INVALID_VOTER = 44007
GROUP_DECISION_OPTIONS_REQUIRED = 44008
GROUP_DECISION_NO_VALID_OPTIONS = 44009
GROUP_DECISION_NOT_OPEN = 44010

AUTH_OTP_INVALID = 41001
AUTH_OTP_EXPIRED = 41002
AUTH_ACCOUNT_LOCKED = 41003
AUTH_TOKEN_REPLAY_DETECTED = 41004
AUTH_SESSION_REVOKED = 41005
AUTH_RECENT_AUTH_REQUIRED = 41006
AUTH_ACCOUNT_EXISTS = 41007
AUTH_ACCOUNT_REQUIRED = 41008
AUTH_INVALID_CREDENTIALS = 41009
AUTH_RESET_CODE_INVALID = 41010
AUTH_OAUTH_BIND_CONFLICT = 41011
AUTH_OAUTH_PROVIDER_UNSUPPORTED = 41012


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

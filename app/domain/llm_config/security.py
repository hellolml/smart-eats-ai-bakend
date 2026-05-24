from __future__ import annotations

import re
from urllib.parse import urlparse


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
]


class LlmConfigSecurityError(ValueError):
    pass


def validate_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise LlmConfigSecurityError("base_url must start with http:// or https://")
    if not parsed.hostname:
        raise LlmConfigSecurityError("base_url host is required")
    return value


def sanitize_error_message(message: str | None, *, api_key: str | None = None) -> str | None:
    if not message:
        return None
    sanitized = str(message)
    if api_key:
        sanitized = sanitized.replace(api_key, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[redacted]", sanitized)
    return sanitized[:500]

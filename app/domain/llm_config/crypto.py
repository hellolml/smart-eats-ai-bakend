from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from app.common.config import settings


def _fernet() -> Fernet:
    raw_key = settings.LLM_CONFIG_ENCRYPTION_KEY or settings.JWT_SECRET
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("api_key is required")
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    value = ciphertext.strip()
    if not value:
        return ""
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def mask_api_key(raw: str) -> str:
    value = raw.strip()
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}****{value[-4:]}"

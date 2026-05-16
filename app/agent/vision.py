from __future__ import annotations

import base64
from typing import Any

from app.common.config import settings


SUPPORTED_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}


async def build_vision_content_parts(
    attachments: list[dict[str, Any]] | None,
    *,
    minio: Any,
) -> list[dict[str, Any]]:
    if not isinstance(attachments, list) or not attachments:
        return []

    parts: list[dict[str, Any]] = []
    max_images = max(0, int(settings.LLM_VISION_MAX_IMAGES or 0))
    max_bytes = max(1, int(settings.LLM_VISION_MAX_IMAGE_BYTES or 1))

    for item in attachments:
        if len(parts) >= max_images:
            break
        if not isinstance(item, dict) or item.get("kind") != "image":
            continue
        object_key = item.get("object_key")
        content_type = str(item.get("content_type") or "").lower()
        if not isinstance(object_key, str) or not object_key:
            continue
        if content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
            continue
        size_bytes = item.get("size_bytes")
        if isinstance(size_bytes, int) and size_bytes > max_bytes:
            continue

        data = await minio.read_bytes(object_key)
        if len(data) > max_bytes:
            continue
        encoded = base64.b64encode(data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{content_type};base64,{encoded}",
                },
            }
        )

    return parts

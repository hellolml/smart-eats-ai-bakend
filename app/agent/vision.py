from __future__ import annotations

import base64
import logging
from typing import Any

from app.common.config import settings

logger = logging.getLogger("agent.vision")

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
    max_images = int(settings.LLM_VISION_MAX_IMAGES or 5)
    if max_images <= 0:
        max_images = 5
    max_bytes = int(settings.LLM_VISION_MAX_IMAGE_BYTES or (10 * 1024 * 1024))
    if max_bytes <= 1:
        max_bytes = 10 * 1024 * 1024

    for item in attachments:
        if len(parts) >= max_images:
            break
        if not isinstance(item, dict) or item.get("kind") != "image":
            logger.debug(
                "vision_skip_not_image object_key=%s kind=%s",
                item.get("object_key") if isinstance(item, dict) else None,
                item.get("kind") if isinstance(item, dict) else None,
            )
            continue
        object_key = item.get("object_key")
        content_type = str(item.get("content_type") or "").lower()
        if not isinstance(object_key, str) or not object_key:
            logger.warning("vision_skip_missing_object_key content_type=%s", content_type)
            continue
        if content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
            logger.warning("vision_skip_unsupported_type object_key=%s content_type=%s", object_key, content_type)
            continue
        size_bytes = item.get("size_bytes")
        if isinstance(size_bytes, int) and size_bytes > max_bytes:
            logger.warning("vision_skip_oversized object_key=%s size=%s max=%s", object_key, size_bytes, max_bytes)
            continue

        data = await minio.read_bytes(object_key)
        if len(data) > max_bytes:
            logger.warning("vision_skip_loaded_oversized object_key=%s size=%s max=%s", object_key, len(data), max_bytes)
            continue
        logger.info("vision_image_loaded object_key=%s content_type=%s data_size=%s", object_key, content_type, len(data))
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

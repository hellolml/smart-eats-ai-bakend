from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.common.config import settings


@dataclass
class MinioStub:
    base_path: Path
    bucket: str

    async def upload_bytes(self, object_key: str, data: bytes) -> str:
        target = self.base_path / self.bucket / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)
        return object_key


_minio_client: MinioStub | None = None


def _get_client() -> MinioStub:
    global _minio_client
    if _minio_client is None:
        base = Path(settings.MINIO_BASE_PATH)
        _minio_client = MinioStub(base_path=base, bucket=settings.MINIO_BUCKET)
    return _minio_client


async def get_minio() -> MinioStub:
    return _get_client()

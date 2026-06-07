import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("EVAL_DATABASE_URL", os.environ["DATABASE_URL"])
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("LANGGRAPH_STORE_BACKEND", "memory")

import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis
from httpx import ASGITransport, AsyncClient

from app.infra.db import init_db
from app.infra.redis import get_redis
from app.main import app
from app.common.config import settings


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    await init_db()
    yield


@pytest_asyncio.fixture(autouse=True)
async def override_redis():
    redis_client = fakeredis.FakeRedis(decode_responses=True)

    async def _get_redis():
        yield redis_client

    app.dependency_overrides[get_redis] = _get_redis
    yield redis_client
    app.dependency_overrides.pop(get_redis, None)
    await redis_client.aclose()


@pytest.fixture(autouse=True)
def disable_background_realtime_eval(monkeypatch):
    monkeypatch.setattr(settings, "REALTIME_EVAL_ENABLED", False)


@pytest_asyncio.fixture()
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

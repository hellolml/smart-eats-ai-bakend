from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool, NullPool
from sqlalchemy import event

from app.common.config import settings
from app.infra.models.base import Base


def _create_engine():
    url = settings.DATABASE_URL
    if url.startswith("sqlite+aiosqlite") and url.endswith(":memory:"):
        return create_async_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if url.startswith("sqlite+aiosqlite"):
        engine = create_async_engine(
            url,
            pool_pre_ping=True,
            echo=False,
            connect_args={"timeout": 30},
            poolclass=NullPool,
        )
        _register_sqlite_pragmas(engine)
        return engine
    return create_async_engine(url, pool_pre_ping=True, echo=False)


def _register_sqlite_pragmas(engine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()


engine = _create_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    from app.infra.models import (
        auth,
        chat,
        context,
        fridge,
        game,
        grocery,
        group_decision,
        llm_config,
        plan,
        preference,
        recipe,
        restaurant,
        user,
    )  # noqa: F401

    async with engine.begin() as conn:
        if settings.DATABASE_URL.startswith("postgresql"):
            await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("postgresql"):
            await conn.exec_driver_sql("ALTER TABLE context_memories ADD COLUMN IF NOT EXISTS embedding vector(384)")
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_context_memories_embedding ON context_memories USING ivfflat (embedding vector_cosine_ops)"
            )
            await conn.exec_driver_sql("ALTER TABLE context_event_embeddings ADD COLUMN IF NOT EXISTS embedding vector(384)")
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_context_event_embeddings_embedding ON context_event_embeddings USING ivfflat (embedding vector_cosine_ops)"
            )
        if settings.DATABASE_URL.startswith("sqlite+aiosqlite"):
            await _ensure_sqlite_columns(conn)


async def _ensure_sqlite_columns(conn) -> None:
    result = await conn.exec_driver_sql("PRAGMA table_info(chat_sessions)")
    cols = {row[1] for row in result.fetchall()}
    if "title" not in cols:
        await conn.exec_driver_sql("ALTER TABLE chat_sessions ADD COLUMN title VARCHAR(255)")
    if "deleted_at" not in cols:
        await conn.exec_driver_sql("ALTER TABLE chat_sessions ADD COLUMN deleted_at DATETIME")

    await conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS user_taste_profile (
            user_id VARCHAR(36) PRIMARY KEY,
            dislikes JSON,
            allergens JSON,
            diet_goal VARCHAR(64),
            budget_range VARCHAR(32),
            spice_level INTEGER,
            confidence FLOAT DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    taste_cols_result = await conn.exec_driver_sql("PRAGMA table_info(user_taste_profile)")
    taste_cols = {row[1] for row in taste_cols_result.fetchall()}
    if "confidence" not in taste_cols:
        await conn.exec_driver_sql("ALTER TABLE user_taste_profile ADD COLUMN confidence FLOAT DEFAULT 0")
    if "updated_at" not in taste_cols:
        await conn.exec_driver_sql(
            "ALTER TABLE user_taste_profile ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
        )

    await conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS preference_events (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36),
            event_name VARCHAR(64),
            payload_json JSON,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS user_llm_provider_configs (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36),
            display_name VARCHAR(120),
            provider_type VARCHAR(32),
            base_url VARCHAR(512),
            api_key_encrypted TEXT,
            api_key_hint VARCHAR(64),
            model_planner VARCHAR(120),
            model_writer VARCHAR(120),
            model_vision_planner VARCHAR(120),
            enabled BOOLEAN DEFAULT 1,
            is_default BOOLEAN DEFAULT 0,
            last_tested_at DATETIME,
            last_test_status VARCHAR(24),
            last_test_error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_user_llm_provider_configs_user_id ON user_llm_provider_configs(user_id)"
    )

    group_session_cols_result = await conn.exec_driver_sql("PRAGMA table_info(group_decision_sessions)")
    group_session_cols = {row[1] for row in group_session_cols_result.fetchall()}
    if "closed_at" not in group_session_cols:
        await conn.exec_driver_sql("ALTER TABLE group_decision_sessions ADD COLUMN closed_at DATETIME")
    if "winner_item_id" not in group_session_cols:
        await conn.exec_driver_sql("ALTER TABLE group_decision_sessions ADD COLUMN winner_item_id VARCHAR(36)")
    if "total_votes" not in group_session_cols:
        await conn.exec_driver_sql("ALTER TABLE group_decision_sessions ADD COLUMN total_votes INTEGER DEFAULT 0")
    if "result_snapshot" not in group_session_cols:
        await conn.exec_driver_sql("ALTER TABLE group_decision_sessions ADD COLUMN result_snapshot JSON")

    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_group_votes_session_voter ON group_votes(session_id, voter_key)"
    )

    user_session_cols_result = await conn.exec_driver_sql("PRAGMA table_info(user_sessions)")
    user_session_cols = {row[1] for row in user_session_cols_result.fetchall()}
    if "session_family_id" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN session_family_id VARCHAR(36)")
    if "current_refresh_jti" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN current_refresh_jti VARCHAR(64)")
    if "refresh_expires_at" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN refresh_expires_at DATETIME")
    if "last_ip" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN last_ip VARCHAR(64)")
    if "status" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN status VARCHAR(24) DEFAULT 'active'")
    if "revoke_reason" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN revoke_reason VARCHAR(64)")
    if "rotation_counter" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN rotation_counter INTEGER DEFAULT 0")
    if "last_seen_at" not in user_session_cols:
        await conn.exec_driver_sql("ALTER TABLE user_sessions ADD COLUMN last_seen_at DATETIME")

    await conn.exec_driver_sql("DROP TABLE IF EXISTS chat_checkpoints")

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.models.llm_config import UserLlmProviderConfig


async def list_user_configs(db: AsyncSession, user_id: str) -> list[UserLlmProviderConfig]:
    result = await db.execute(
        select(UserLlmProviderConfig)
        .where(UserLlmProviderConfig.user_id == user_id)
        .order_by(UserLlmProviderConfig.is_default.desc(), UserLlmProviderConfig.created_at.desc())
    )
    return list(result.scalars().all())


async def get_user_config(db: AsyncSession, user_id: str, config_id: str) -> UserLlmProviderConfig | None:
    result = await db.execute(
        select(UserLlmProviderConfig).where(
            UserLlmProviderConfig.id == config_id,
            UserLlmProviderConfig.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_default_config(db: AsyncSession, user_id: str) -> UserLlmProviderConfig | None:
    result = await db.execute(
        select(UserLlmProviderConfig).where(
            UserLlmProviderConfig.user_id == user_id,
            UserLlmProviderConfig.enabled.is_(True),
            UserLlmProviderConfig.is_default.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def add_user_config(db: AsyncSession, config: UserLlmProviderConfig) -> UserLlmProviderConfig:
    if config.is_default:
        await clear_default_config(db, config.user_id)
    db.add(config)
    await db.flush()
    await db.refresh(config)
    return config


async def clear_default_config(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        update(UserLlmProviderConfig)
        .where(UserLlmProviderConfig.user_id == user_id, UserLlmProviderConfig.is_default.is_(True))
        .values(is_default=False)
    )


async def set_default_config(db: AsyncSession, config: UserLlmProviderConfig) -> UserLlmProviderConfig:
    await clear_default_config(db, config.user_id)
    config.is_default = True
    config.enabled = True
    await db.flush()
    await db.refresh(config)
    return config


async def delete_user_config(db: AsyncSession, config: UserLlmProviderConfig) -> None:
    await db.delete(config)
    await db.flush()

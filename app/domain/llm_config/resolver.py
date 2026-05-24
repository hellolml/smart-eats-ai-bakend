from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm_adapters import ProviderRegistry
from app.common.config import settings
from app.domain.llm_config.crypto import decrypt_api_key
from app.domain.llm_config.repository import get_user_config, list_user_configs
from app.domain.llm_config.schemas import ResolvedModelConfig


ENV_PROVIDERS = {"qwen", "deepseek", "openai"}


async def resolve_model_config(
    db: AsyncSession,
    user_id: str | None,
    model_value: str | None,
) -> ResolvedModelConfig:
    value = (model_value or "").strip()
    if value.startswith("config:"):
        if not user_id:
            raise HTTPException(status_code=401, detail="authentication required")
        return await _resolve_user_config(db, user_id, value)

    if user_id:
        preferred_config = await _get_preferred_user_config(db, user_id)
        if preferred_config:
            return _resolved_from_user_config(preferred_config, preferred_config.model_planner)

    if value.startswith("env:"):
        return _resolve_env_config(value.removeprefix("env:"))
    if value:
        return _resolve_env_config(value)
    return _resolve_env_config(settings.LLM_PROVIDER)


def resolve_env_provider_value(model_value: str | None) -> str | None:
    value = (model_value or "").strip()
    if not value:
        return None
    if value.startswith("env:"):
        value = value.removeprefix("env:")
    provider_key, _, model_id = value.partition(":")
    provider_key = provider_key.strip().lower()
    model_id = model_id.strip()
    if provider_key not in ENV_PROVIDERS:
        return None
    configured_providers = {
        item.strip().lower()
        for item in (settings.LLM_PROVIDERS or "").split(",")
        if isinstance(item, str) and item.strip()
    }
    if configured_providers and provider_key not in configured_providers:
        return None
    if not model_id:
        return provider_key
    return f"{provider_key}:{model_id}"


async def _get_preferred_user_config(db: AsyncSession, user_id: str):
    configs = await list_user_configs(db, user_id)
    for config in configs:
        if config.enabled:
            return config
    return None


async def _resolve_user_config(db: AsyncSession, user_id: str, value: str) -> ResolvedModelConfig:
    parts = value.split(":", 2)
    if len(parts) < 2 or not parts[1].strip():
        raise HTTPException(status_code=400, detail="invalid model config selection")
    config_id = parts[1].strip()
    requested_model = parts[2].strip() if len(parts) == 3 else ""
    config = await get_user_config(db, user_id, config_id)
    if not config or not config.enabled:
        raise HTTPException(status_code=404, detail="model config not found")
    return _resolved_from_user_config(config, requested_model or config.model_planner)


def _resolved_from_user_config(config, model: str) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        source="user_config",
        provider=config.provider_type or "openai_compatible",
        config_id=config.id,
        display_name=config.display_name,
        base_url=config.base_url,
        api_key=decrypt_api_key(config.api_key_encrypted),
        model_planner=model,
        model_writer=config.model_writer or model,
        model_vision_planner=config.model_vision_planner,
    )


def _resolve_env_config(value: str | None) -> ResolvedModelConfig:
    provider_value = resolve_env_provider_value(value) or settings.LLM_PROVIDER
    provider_config = ProviderRegistry.get(provider_value)
    return ResolvedModelConfig(
        source="env",
        provider=provider_config.name,
        provider_value=provider_value,
        model_planner=provider_config.model_planner,
        model_writer=provider_config.model_writer,
        model_vision_planner=provider_config.model_vision_planner,
    )

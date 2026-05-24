from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
import httpx
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import settings
from app.domain.llm_config.crypto import decrypt_api_key, encrypt_api_key, mask_api_key
from app.domain.llm_config.repository import (
    add_user_config,
    delete_user_config,
    get_user_config,
    list_user_configs,
    set_default_config,
)
from app.domain.llm_config.schemas import (
    LlmProviderConfigCreate,
    LlmProviderConfigPublic,
    LlmProviderConfigTestRequest,
    LlmProviderConfigTestResult,
    LlmProviderConfigUpdate,
)
from app.domain.llm_config.security import LlmConfigSecurityError, sanitize_error_message, validate_base_url
from app.infra.models.llm_config import UserLlmProviderConfig


class LlmConfigService:
    @staticmethod
    async def list_configs(db: AsyncSession, user_id: str) -> list[LlmProviderConfigPublic]:
        rows = await list_user_configs(db, user_id)
        return [LlmConfigService.to_public(row) for row in rows]

    @staticmethod
    async def create_config(
        db: AsyncSession,
        user_id: str,
        payload: LlmProviderConfigCreate,
    ) -> LlmProviderConfigPublic:
        base_url = LlmConfigService._validate_base_url(payload.base_url)
        config = UserLlmProviderConfig(
            id=str(uuid4()),
            user_id=user_id,
            display_name=payload.display_name,
            provider_type=payload.provider_type,
            base_url=base_url,
            api_key_encrypted=encrypt_api_key(payload.api_key),
            api_key_hint=mask_api_key(payload.api_key),
            model_planner=payload.model_planner,
            model_writer=payload.model_writer,
            model_vision_planner=payload.model_vision_planner,
            enabled=payload.enabled,
            is_default=payload.is_default,
        )
        await add_user_config(db, config)
        await db.commit()
        await db.refresh(config)
        return LlmConfigService.to_public(config)

    @staticmethod
    async def update_config(
        db: AsyncSession,
        user_id: str,
        config_id: str,
        payload: LlmProviderConfigUpdate,
    ) -> LlmProviderConfigPublic:
        config = await LlmConfigService._get_owned_config(db, user_id, config_id)
        updates = payload.model_dump(exclude_unset=True)
        if "base_url" in updates and updates["base_url"] is not None:
            config.base_url = LlmConfigService._validate_base_url(updates["base_url"])
        if "api_key" in updates and updates["api_key"]:
            config.api_key_encrypted = encrypt_api_key(updates["api_key"])
            config.api_key_hint = mask_api_key(updates["api_key"])
        if "display_name" in updates and updates["display_name"] is not None:
            config.display_name = updates["display_name"]
        if "provider_type" in updates and updates["provider_type"] is not None:
            config.provider_type = updates["provider_type"]
        if "model_planner" in updates and updates["model_planner"] is not None:
            config.model_planner = updates["model_planner"]
        if "model_writer" in updates:
            config.model_writer = updates["model_writer"]
        if "model_vision_planner" in updates:
            config.model_vision_planner = updates["model_vision_planner"]
        if "enabled" in updates and updates["enabled"] is not None:
            config.enabled = updates["enabled"]
            if not config.enabled:
                config.is_default = False
        if updates.get("is_default") is True:
            await set_default_config(db, config)
        elif updates.get("is_default") is False:
            config.is_default = False

        await db.commit()
        await db.refresh(config)
        return LlmConfigService.to_public(config)

    @staticmethod
    async def delete_config(db: AsyncSession, user_id: str, config_id: str) -> dict[str, bool]:
        config = await LlmConfigService._get_owned_config(db, user_id, config_id)
        await delete_user_config(db, config)
        await db.commit()
        return {"deleted": True}

    @staticmethod
    async def set_default(db: AsyncSession, user_id: str, config_id: str) -> LlmProviderConfigPublic:
        config = await LlmConfigService._get_owned_config(db, user_id, config_id)
        await set_default_config(db, config)
        await db.commit()
        await db.refresh(config)
        return LlmConfigService.to_public(config)

    @staticmethod
    async def test_config(
        db: AsyncSession,
        user_id: str,
        payload: LlmProviderConfigTestRequest,
    ) -> LlmProviderConfigTestResult:
        config: UserLlmProviderConfig | None = None
        if payload.config_id:
            config = await LlmConfigService._get_owned_config(db, user_id, payload.config_id)
            base_url = config.base_url
            api_key = decrypt_api_key(config.api_key_encrypted)
            provider_type = config.provider_type or "openai_compatible"
            model = payload.model or config.model_planner
        else:
            base_url = LlmConfigService._validate_base_url(payload.base_url or "")
            api_key = payload.api_key or ""
            provider_type = payload.provider_type
            default_model = "claude-sonnet-4-6" if provider_type == "anthropic" else "gpt-4o-mini"
            model = payload.model or default_model

        status = "success"
        error: str | None = None
        try:
            if provider_type == "anthropic":
                await LlmConfigService._test_anthropic_messages(base_url, api_key, model)
            else:
                client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=settings.LLM_CONFIG_TEST_TIMEOUT_SECONDS,
                )
                await client.models.list()
        except Exception as exc:
            status = "failed"
            error = sanitize_error_message(str(exc), api_key=api_key)

        if config is not None:
            config.last_tested_at = datetime.now(timezone.utc)
            config.last_test_status = status
            config.last_test_error = error
            await db.commit()

        return LlmProviderConfigTestResult(status=status, error=error)

    @staticmethod
    async def _test_anthropic_messages(base_url: str, api_key: str, model: str) -> None:
        root = base_url.rstrip("/")
        url = f"{root}/messages" if root.endswith("/v1") else f"{root}/v1/messages"
        async with httpx.AsyncClient(timeout=settings.LLM_CONFIG_TEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
            response.raise_for_status()

    @staticmethod
    def to_public(config: UserLlmProviderConfig) -> LlmProviderConfigPublic:
        return LlmProviderConfigPublic.model_validate(config)

    @staticmethod
    async def _get_owned_config(db: AsyncSession, user_id: str, config_id: str) -> UserLlmProviderConfig:
        config = await get_user_config(db, user_id, config_id)
        if not config:
            raise HTTPException(status_code=404, detail="model config not found")
        return config

    @staticmethod
    def _validate_base_url(base_url: str) -> str:
        try:
            return validate_base_url(base_url)
        except LlmConfigSecurityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

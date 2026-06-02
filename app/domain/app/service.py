from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from uuid import uuid4

import httpx
import redis.asyncio as redis
from fastapi import HTTPException
from openai import AsyncOpenAI
from redis.exceptions import RedisError
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.state import ChatState
from app.agent import conversation
from app.agent.llm_adapters import ProviderRegistry
from app.agent.multi_agent import AgentRouter
from app.common.config import settings
from app.common.errors import (
    AUTH_ACCOUNT_EXISTS,
    AUTH_ACCOUNT_LOCKED,
    AUTH_ACCOUNT_REQUIRED,
    AUTH_INVALID_CREDENTIALS,
    AUTH_OAUTH_BIND_CONFLICT,
    AUTH_OAUTH_PROVIDER_UNSUPPORTED,
    AUTH_OTP_INVALID,
    AUTH_RESET_CODE_INVALID,
    AUTH_SESSION_REVOKED,
    AUTH_TOKEN_REPLAY_DETECTED,
    AppError,
    REDIS_UNAVAILABLE,
)
from app.common.rate_limit import ensure_rate_limit
from app.common.security import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.domain.app.mappers import (
    map_home_chef_recipe,
    map_ingredient,
    map_me,
    map_preferences,
    map_restaurant,
)
from app.domain.llm_config.repository import list_user_configs
from app.domain.llm_config.resolver import resolve_model_config
from app.domain.game.blindbox_map import map_blindbox_result
from app.domain.preferences.markdown_profile import ensure_user_preference_file
from app.domain.recipe.service import RecipeService
from app.domain.restaurant.service import RestaurantService
from app.infra.models.chat import ChatMessage, ChatSession
from app.infra.models.fridge import FridgeItem, FridgePhoto, RecognitionJob
from app.infra.models.game import BlindboxRoll, WheelConfig, WheelSpin
from app.infra.models.auth import AuthEvent, OAuthAccount, UserSession
from app.infra.models.grocery import GroceryList, GroceryListItem
from app.infra.models.plan import TravelPlan
from app.infra.models.preference import UserPreference, UserProfile
from app.infra.models.user import User
from app.infra.external.amap import amap

logger = logging.getLogger(__name__)


class AppBffService:
    @staticmethod
    def _map_travel_plan(plan: TravelPlan) -> dict[str, Any]:
        return {
            "id": plan.id,
            "user_id": plan.user_id,
            "session_id": plan.session_id,
            "title": plan.title,
            "plan_type": plan.plan_type,
            "status": plan.status,
            "date_text": plan.date_text,
            "source_text": plan.source_text or "",
            "qr_code_url": plan.qr_code_url,
            "schema_url": plan.schema_url,
            "plan_json": plan.plan_json or {},
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        }

    @staticmethod
    async def list_plans(user_id: str, db: AsyncSession) -> list[dict[str, Any]]:
        result = await db.execute(
            select(TravelPlan)
            .where(TravelPlan.user_id == user_id)
            .order_by(desc(TravelPlan.created_at))
        )
        return [AppBffService._map_travel_plan(item) for item in result.scalars().all()]

    @staticmethod
    async def create_plan(user_id: str, payload: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
        plan = TravelPlan(
            id=str(uuid4()),
            user_id=user_id,
            session_id=payload.get("session_id"),
            title=(payload.get("title") or "旅行计划")[:160],
            plan_type=(payload.get("plan_type") or "travel")[:32],
            status=(payload.get("status") or "saved")[:32],
            date_text=payload.get("date_text"),
            source_text=payload.get("source_text"),
            qr_code_url=payload.get("qr_code_url"),
            schema_url=payload.get("schema_url"),
            plan_json=payload.get("plan_json") if isinstance(payload.get("plan_json"), dict) else {},
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        return AppBffService._map_travel_plan(plan)

    @staticmethod
    async def delete_plan(user_id: str, plan_id: str, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(TravelPlan).where(
                TravelPlan.id == plan_id,
                TravelPlan.user_id == user_id,
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise HTTPException(status_code=404, detail="plan not found")
        await db.delete(plan)
        await db.commit()
        return {"deleted": True, "id": plan_id}

    @staticmethod
    def list_chat_models() -> dict[str, Any]:
        configured_providers = [
            item.strip().lower()
            for item in (settings.LLM_PROVIDERS or "").split(",")
            if isinstance(item, str) and item.strip()
        ]
        default_provider = (settings.LLM_PROVIDER or "qwen").strip().lower()
        if default_provider and default_provider not in configured_providers:
            configured_providers.append(default_provider)

        model_aliases = {
            "qwen3.5-flash": "Qwen3.5-Flash",
            "qwen3.5-plus": "Qwen3.5-Plus",
            "qwen3.5-flash-2026-02-23": "Qwen3.5-Flash-2026-02-23",
            "qwen3.5-plus-2026-02-15": "Qwen3.5-Plus-2026-02-15",
            "qwen3.5-397b-a17b": "Qwen3.5-397b-a17b",
        }

        parsed_model_map: dict[str, list[str]] = {}
        raw_model_pairs = [
            item.strip()
            for item in (settings.LLM_MODELS or "").split(",")
            if isinstance(item, str) and item.strip()
        ]
        for pair in raw_model_pairs:
            if ":" not in pair:
                continue
            provider_key, model_id = pair.split(":", 1)
            provider_key = provider_key.strip().lower()
            model_id = model_id.strip()
            if not provider_key or not model_id:
                continue
            parsed_model_map.setdefault(provider_key, [])
            if model_id not in parsed_model_map[provider_key]:
                parsed_model_map[provider_key].append(model_id)

        display_rows: list[dict[str, str]] = []
        allowed_set: set[str] = set()
        provider_to_defaults: dict[str, tuple[str, str]] = {
            "qwen": (settings.QWEN_MODEL_PLANNER, settings.QWEN_MODEL_WRITER),
            "deepseek": (settings.DEEPSEEK_MODEL_PLANNER, settings.DEEPSEEK_MODEL_WRITER),
            "openai": (settings.OPENAI_MODEL_PLANNER, settings.OPENAI_MODEL_WRITER),
        }

        for provider in configured_providers:
            if provider not in provider_to_defaults:
                continue
            provider_cfg = ProviderRegistry.get(provider)
            planner_model, writer_model = provider_to_defaults[provider]
            defaults = [model for model in (planner_model, writer_model) if isinstance(model, str) and model.strip()]
            configured_models = parsed_model_map.get(provider, [])
            merged_models: list[str] = []
            for model in [*configured_models, *defaults]:
                if model and model not in merged_models:
                    merged_models.append(model)

            for model_id in merged_models:
                value = f"{provider}:{model_id}"
                allowed_set.add(value)
                display_rows.append(
                    {
                        "value": value,
                        "source": "env",
                        "provider": provider,
                        "model": model_id,
                        "label": model_aliases.get(model_id, model_id),
                        "provider_label": provider_cfg.name,
                    }
                )

        if not display_rows:
            fallback_provider = ProviderRegistry.get(default_provider)
            fallback_model = fallback_provider.model_planner
            value = f"{fallback_provider.name}:{fallback_model}"
            allowed_set.add(value)
            display_rows.append(
                {
                    "value": value,
                    "source": "env",
                    "provider": fallback_provider.name,
                    "model": fallback_model,
                    "label": model_aliases.get(fallback_model, fallback_model),
                    "provider_label": fallback_provider.name,
                }
            )

        default_provider_cfg = ProviderRegistry.get(default_provider)
        default_model_value = f"{default_provider_cfg.name}:{default_provider_cfg.model_planner}"
        if default_model_value not in allowed_set and display_rows:
            default_model_value = display_rows[0]["value"]

        return {
            "models": display_rows,
            "default": default_model_value,
            "providers": configured_providers,
        }

    @staticmethod
    async def list_chat_models_for_user(db: AsyncSession, user_id: str | None) -> dict[str, Any]:
        data = AppBffService.list_chat_models()
        if not user_id:
            return data

        user_configs = [config for config in await list_user_configs(db, user_id) if config.enabled]
        if not user_configs:
            return data

        models: list[dict[str, Any]] = []
        providers: list[str] = []
        default_value: str | None = None
        first_value: str | None = None
        for config in user_configs:
            value = f"config:{config.id}:{config.model_planner}"
            first_value = first_value or value
            models.append(
                {
                    "value": value,
                    "source": "user_config",
                    "config_id": config.id,
                    "provider": config.provider_type,
                    "model": config.model_planner,
                    "label": f"{config.display_name} / {config.model_planner}",
                    "provider_label": config.display_name,
                    "is_default": config.is_default,
                }
            )
            if config.provider_type not in providers:
                providers.append(config.provider_type)
            if config.is_default:
                default_value = value

        return {
            "models": models,
            "default": default_value or first_value,
            "providers": providers,
        }

    @staticmethod
    def resolve_chat_provider(model_value: str | None) -> str | None:
        if not isinstance(model_value, str):
            return None
        value = model_value.strip()
        if value.startswith("env:"):
            value = value.removeprefix("env:")
        if not value or value.startswith("config:"):
            return None
        provider_key, _, model_id = value.partition(":")
        provider_key = provider_key.strip().lower()
        model_id = model_id.strip()
        if provider_key not in {"qwen", "deepseek", "openai"}:
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

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    async def _log_auth_event(
        db: AsyncSession,
        *,
        event_type: str,
        user_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            db.add(
                AuthEvent(
                    id=str(uuid4()),
                    user_id=user_id,
                    event_type=event_type[:64],
                    ip=(ip or "")[:64] or None,
                    user_agent=(user_agent or "")[:255] or None,
                    payload_json=payload or {},
                )
            )
            await db.flush()
        except Exception:
            logger.exception("failed to log auth event: %s", event_type)

    @staticmethod
    async def _send_sms_code(phone: str, code: str, purpose: str) -> dict[str, Any]:
        provider = (settings.SMS_PROVIDER or "mock").lower().strip()
        if provider == "mock":
            resp: dict[str, Any] = {"sent": True, "provider": "mock"}
            if settings.DEBUG:
                resp["debug_code"] = code
            return resp

        if provider == "webhook":
            if not settings.SMS_WEBHOOK_URL:
                raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="sms webhook not configured", http_status=400)
            headers = {"Content-Type": "application/json"}
            if settings.SMS_WEBHOOK_TOKEN:
                headers["Authorization"] = f"Bearer {settings.SMS_WEBHOOK_TOKEN}"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    settings.SMS_WEBHOOK_URL,
                    headers=headers,
                    json={
                        "phone": phone,
                        "code": code,
                        "purpose": purpose,
                        "sign_name": settings.SMS_SIGN_NAME,
                        "template_code": settings.SMS_TEMPLATE_CODE,
                    },
                )
                r.raise_for_status()
            return {"sent": True, "provider": "webhook"}

        raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="sms provider unsupported", http_status=400)

    @staticmethod
    async def _send_email_code(email: str, code: str, purpose: str) -> dict[str, Any]:
        provider = (settings.EMAIL_PROVIDER or "mock").lower().strip()
        if provider == "mock":
            resp: dict[str, Any] = {"sent": True, "provider": "mock"}
            if settings.DEBUG:
                resp["debug_code"] = code
            return resp

        subject = "Smart-Eats 验证码"
        body = f"您的验证码是：{code}，用途：{purpose}，10分钟内有效。"

        if provider == "webhook":
            if not settings.EMAIL_WEBHOOK_URL:
                raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="email webhook not configured", http_status=400)
            headers = {"Content-Type": "application/json"}
            if settings.EMAIL_WEBHOOK_TOKEN:
                headers["Authorization"] = f"Bearer {settings.EMAIL_WEBHOOK_TOKEN}"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    settings.EMAIL_WEBHOOK_URL,
                    headers=headers,
                    json={"email": email, "subject": subject, "content": body, "purpose": purpose},
                )
                r.raise_for_status()
            return {"sent": True, "provider": "webhook"}

        if provider == "smtp":
            if not settings.SMTP_HOST or not settings.SMTP_FROM:
                raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="smtp not configured", http_status=400)
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_FROM
            msg["To"] = email
            msg.set_content(body)
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                if settings.SMTP_USE_TLS:
                    server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASS:
                    server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)
            return {"sent": True, "provider": "smtp"}

        raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="email provider unsupported", http_status=400)

    @staticmethod
    async def _send_code_by_account(account: str, code: str, purpose: str) -> dict[str, Any]:
        if "@" in account:
            return await AppBffService._send_email_code(account, code, purpose)
        return await AppBffService._send_sms_code(account, code, purpose)

    @staticmethod
    async def _verify_one_click_token(token: str) -> str:
        provider = (settings.ONECLICK_PROVIDER or "mock").lower().strip()
        if provider == "mock":
            if settings.DEBUG and token.startswith("mock:"):
                phone = token.split(":", 1)[1].strip()
                if phone:
                    return phone
            raise AppError(code=AUTH_INVALID_CREDENTIALS, message="one-click token invalid", http_status=401)

        if provider == "webhook":
            if not settings.ONECLICK_WEBHOOK_URL:
                raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="one-click webhook not configured", http_status=400)
            headers = {"Content-Type": "application/json"}
            if settings.ONECLICK_WEBHOOK_TOKEN:
                headers["Authorization"] = f"Bearer {settings.ONECLICK_WEBHOOK_TOKEN}"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(settings.ONECLICK_WEBHOOK_URL, headers=headers, json={"token": token})
                r.raise_for_status()
                data = r.json() if r.content else {}
            phone = str(data.get("phone") or "").strip()
            if not phone:
                raise AppError(code=AUTH_INVALID_CREDENTIALS, message="one-click token invalid", http_status=401)
            return phone

        raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="one-click provider unsupported", http_status=400)

    @staticmethod
    async def issue_tokens(
        user_id: str,
        redis_client: redis.Redis,
        db: AsyncSession,
        *,
        ip: str | None = None,
        device_info: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        access_token, _ = create_access_token(user_id)

        if session_id:
            existing = (await db.execute(select(UserSession).where(UserSession.id == session_id))).scalar_one_or_none()
            if existing is None:
                session_id = None

        if session_id is None:
            session_id = str(uuid4())
            family_id = str(uuid4())
            session = UserSession(
                id=session_id,
                user_id=user_id,
                session_family_id=family_id,
                refresh_token_hash="",
                device_info=(device_info or "")[:255] or None,
                ip=ip,
                last_ip=ip,
                status="active",
                rotation_counter=0,
                last_seen_at=now,
            )
            db.add(session)
        else:
            session = (await db.execute(select(UserSession).where(UserSession.id == session_id))).scalar_one()
            family_id = session.session_family_id or str(uuid4())
            session.session_family_id = family_id
            session.last_seen_at = now
            session.last_ip = ip
            if device_info:
                session.device_info = device_info[:255]
            session.status = "active"
            session.revoked_at = None
            session.revoke_reason = None

        rotation = int(session.rotation_counter or 0) + 1
        refresh_token, refresh_jti = create_refresh_token(
            user_id,
            session_id=session_id,
            family_id=family_id,
            rotation=rotation,
        )

        old_jti = session.current_refresh_jti
        session.current_refresh_jti = refresh_jti
        session.refresh_token_hash = AppBffService._sha256(refresh_jti)
        session.rotation_counter = rotation
        session.refresh_expires_at = now + timedelta(seconds=settings.REFRESH_TOKEN_TTL_SECONDS)

        await db.commit()

        try:
            await redis_client.setex(f"rt:{refresh_jti}", settings.REFRESH_TOKEN_TTL_SECONDS, user_id)
            if old_jti:
                await redis_client.setex(f"rtu:{old_jti}", settings.REFRESH_TOKEN_TTL_SECONDS, session_id)
        except RedisError as exc:
            logger.warning("refresh token persistence skipped: redis unavailable user_id=%s err=%s", user_id, exc)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "session_id": session_id,
        }

    @staticmethod
    def _ensure_identity_channel_enabled(*, email: str | None = None, phone: str | None = None) -> None:
        if email and not settings.APP_AUTH_EMAIL_ENABLED:
            raise HTTPException(status_code=404, detail="not found")
        if phone and not settings.APP_AUTH_PHONE_ENABLED:
            raise HTTPException(status_code=404, detail="not found")

    @staticmethod
    def _ensure_account_channel_enabled(account: str) -> None:
        if "@" in account:
            AppBffService._ensure_identity_channel_enabled(email=account)
            return
        AppBffService._ensure_identity_channel_enabled(phone=account)

    @staticmethod
    async def register(payload: dict[str, Any], db: AsyncSession, redis_client: redis.Redis, client_ip: str) -> dict[str, Any]:
        # Backward-compatible direct registration
        email = payload.get("email")
        phone = payload.get("phone")
        if not email and not phone:
            raise AppError(code=AUTH_ACCOUNT_REQUIRED, message="email or phone required", http_status=400)
        AppBffService._ensure_identity_channel_enabled(email=email, phone=phone)

        conditions = []
        if email:
            conditions.append(User.email == email)
        if phone:
            conditions.append(User.phone == phone)

        result = await db.execute(select(User).where(or_(*conditions)))
        if result.scalar_one_or_none() is not None:
            raise AppError(code=AUTH_ACCOUNT_EXISTS, message="email or phone already exists", http_status=400)

        user = User(
            id=str(uuid4()),
            email=email,
            phone=phone,
            nickname=payload.get("name") or email or phone or "user",
            password_hash=hash_password(payload["password"]),
        )
        db.add(user)
        await AppBffService._log_auth_event(
            db,
            event_type="register_success",
            user_id=user.id,
            ip=client_ip,
            payload={"source": "direct_register"},
        )
        await ensure_user_preference_file(user.id)
        await db.commit()
        return await AppBffService.issue_tokens(user.id, redis_client, db, ip=client_ip)

    @staticmethod
    async def register_request_otp(payload: dict[str, Any], db: AsyncSession, redis_client: redis.Redis) -> dict[str, Any]:
        email = payload.get("email")
        phone = payload.get("phone")
        if not email and not phone:
            raise AppError(code=AUTH_ACCOUNT_REQUIRED, message="email or phone required", http_status=400)

        AppBffService._ensure_identity_channel_enabled(email=email, phone=phone)
        account = str(email or phone)
        conditions = []
        if email:
            conditions.append(User.email == email)
        if phone:
            conditions.append(User.phone == phone)
        existing = (await db.execute(select(User).where(or_(*conditions)))).scalar_one_or_none()
        if existing is not None:
            raise AppError(code=AUTH_ACCOUNT_EXISTS, message="email or phone already exists", http_status=400)

        code = f"{random.randint(0, 999999):06d}"
        await redis_client.setex(f"otp:register:{account}", 10 * 60, AppBffService._sha256(code))
        await redis_client.setex(f"otp:register:attempts:{account}", 10 * 60, "0")
        send_resp = await AppBffService._send_code_by_account(account, code, "register")
        return {"sent": True, **send_resp}

    @staticmethod
    async def register_confirm(
        payload: dict[str, Any],
        db: AsyncSession,
        redis_client: redis.Redis,
        client_ip: str,
    ) -> dict[str, Any]:
        email = payload.get("email")
        phone = payload.get("phone")
        code = str(payload.get("code") or "")
        if not email and not phone:
            raise AppError(code=AUTH_ACCOUNT_REQUIRED, message="email or phone required", http_status=400)
        if not code:
            raise AppError(code=AUTH_OTP_INVALID, message="code required", http_status=400)
        AppBffService._ensure_identity_channel_enabled(email=email, phone=phone)
        account = str(email or phone)

        code_hash = await redis_client.get(f"otp:register:{account}")
        if not code_hash:
            raise AppError(code=AUTH_OTP_INVALID, message="register code invalid or expired", http_status=400)
        attempts_key = f"otp:register:attempts:{account}"
        attempts = await redis_client.incr(attempts_key)
        if attempts > 5:
            await redis_client.delete(f"otp:register:{account}")
            raise AppError(code=AUTH_OTP_INVALID, message="register code invalid or expired", http_status=400)
        if AppBffService._sha256(code) != code_hash:
            raise AppError(code=AUTH_OTP_INVALID, message="register code invalid or expired", http_status=400)

        conditions = []
        if email:
            conditions.append(User.email == email)
        if phone:
            conditions.append(User.phone == phone)
        existing = (await db.execute(select(User).where(or_(*conditions)))).scalar_one_or_none()
        if existing is not None:
            raise AppError(code=AUTH_ACCOUNT_EXISTS, message="email or phone already exists", http_status=400)

        user = User(
            id=str(uuid4()),
            email=email,
            phone=phone,
            nickname=payload.get("name") or email or phone or "user",
            password_hash=hash_password(payload["password"]),
        )
        db.add(user)
        await ensure_user_preference_file(user.id)
        await db.commit()
        await redis_client.delete(f"otp:register:{account}")
        await redis_client.delete(attempts_key)
        return await AppBffService.issue_tokens(user.id, redis_client, db, ip=client_ip)

    @staticmethod
    async def login(
        payload: dict[str, Any],
        db: AsyncSession,
        redis_client: redis.Redis,
        client_ip: str,
    ) -> dict[str, Any]:
        account = payload["account"]
        AppBffService._ensure_account_channel_enabled(account)
        await ensure_rate_limit(
            redis_client,
            key=f"rl:app_login:{client_ip}:{account}",
            limit=10,
            window_seconds=60,
        )

        lock_key = f"auth:lock:{account}"
        fail_key = f"auth:fail:{account}"
        if await redis_client.exists(lock_key):
            raise AppError(code=AUTH_ACCOUNT_LOCKED, message="account temporarily locked", http_status=423)

        if "@" in account:
            result = await db.execute(select(User).where(User.email == account))
        else:
            result = await db.execute(select(User).where(User.phone == account))

        user = result.scalar_one_or_none()
        if user is None or not verify_password(payload["password"], user.password_hash):
            failures = await redis_client.incr(fail_key)
            if failures == 1:
                await redis_client.expire(fail_key, 3600)
            if failures >= 5:
                await redis_client.setex(lock_key, 15 * 60, "1")
            await AppBffService._log_auth_event(
                db,
                event_type="login_failed",
                user_id=getattr(user, "id", None),
                ip=client_ip,
                payload={"account": account, "failures": int(failures)},
            )
            await db.commit()
            raise AppError(code=AUTH_INVALID_CREDENTIALS, message="invalid credentials", http_status=401)

        await redis_client.delete(fail_key)
        await AppBffService._log_auth_event(
            db,
            event_type="login_success",
            user_id=user.id,
            ip=client_ip,
            payload={"account": account},
        )
        await db.commit()
        return await AppBffService.issue_tokens(user.id, redis_client, db, ip=client_ip)

    @staticmethod
    async def login_otp_request(account: str, redis_client: redis.Redis, db: AsyncSession) -> dict[str, Any]:
        target = (account or "").strip()
        if not target:
            raise AppError(code=AUTH_ACCOUNT_REQUIRED, message="account required", http_status=400)
        AppBffService._ensure_account_channel_enabled(target)

        if "@" in target:
            user = (await db.execute(select(User).where(User.email == target))).scalar_one_or_none()
            if user is None:
                user = User(
                    id=str(uuid4()),
                    email=target,
                    phone=None,
                    nickname=(target.split("@")[0] or "用户")[:64],
                    password_hash=hash_password(str(uuid4())),
                )
                db.add(user)
                await db.commit()
        else:
            user = (await db.execute(select(User).where(User.phone == target))).scalar_one_or_none()
            if user is None:
                user = User(
                    id=str(uuid4()),
                    email=None,
                    phone=target,
                    nickname=f"用户{target[-4:]}" if len(target) >= 4 else "用户",
                    password_hash=hash_password(str(uuid4())),
                )
                db.add(user)
                await db.commit()

        code = f"{random.randint(0, 999999):06d}"
        await redis_client.setex(f"otp:login:{target}", 10 * 60, AppBffService._sha256(code))
        await redis_client.setex(f"otp:login:attempts:{target}", 10 * 60, "0")
        send_resp = await AppBffService._send_code_by_account(target, code, "login")
        return {"sent": True, **send_resp}

    @staticmethod
    async def login_otp_confirm(
        account: str,
        code: str,
        redis_client: redis.Redis,
        db: AsyncSession,
        client_ip: str,
    ) -> dict[str, Any]:
        target = (account or "").strip()
        if not target:
            raise AppError(code=AUTH_ACCOUNT_REQUIRED, message="account required", http_status=400)
        AppBffService._ensure_account_channel_enabled(target)

        code_hash = await redis_client.get(f"otp:login:{target}")
        if not code_hash:
            raise AppError(code=AUTH_OTP_INVALID, message="login code invalid or expired", http_status=400)

        attempts_key = f"otp:login:attempts:{target}"
        attempts = await redis_client.incr(attempts_key)
        if attempts > 5:
            await redis_client.delete(f"otp:login:{target}")
            raise AppError(code=AUTH_OTP_INVALID, message="login code invalid or expired", http_status=400)

        if AppBffService._sha256(code) != code_hash:
            raise AppError(code=AUTH_OTP_INVALID, message="login code invalid or expired", http_status=400)

        if "@" in target:
            user = (await db.execute(select(User).where(User.email == target))).scalar_one_or_none()
            if user is None:
                user = User(
                    id=str(uuid4()),
                    email=target,
                    phone=None,
                    nickname=(target.split("@")[0] or "用户")[:64],
                    password_hash=hash_password(str(uuid4())),
                )
                db.add(user)
                await db.commit()
        else:
            user = (await db.execute(select(User).where(User.phone == target))).scalar_one_or_none()
            if user is None:
                user = User(
                    id=str(uuid4()),
                    email=None,
                    phone=target,
                    nickname=f"用户{target[-4:]}" if len(target) >= 4 else "用户",
                    password_hash=hash_password(str(uuid4())),
                )
                db.add(user)
                await db.commit()

        await redis_client.delete(f"otp:login:{target}")
        await redis_client.delete(attempts_key)
        await AppBffService._log_auth_event(
            db,
            event_type="otp_login_success",
            user_id=user.id,
            ip=client_ip,
            payload={"account": target},
        )
        await db.commit()
        return await AppBffService.issue_tokens(user.id, redis_client, db, ip=client_ip)

    @staticmethod
    async def login_one_click(token: str, redis_client: redis.Redis, db: AsyncSession, client_ip: str) -> dict[str, Any]:
        phone = await AppBffService._verify_one_click_token(token)
        AppBffService._ensure_identity_channel_enabled(phone=phone)
        user = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
        if user is None:
            user = User(
                id=str(uuid4()),
                email=None,
                phone=phone,
                nickname=f"用户{phone[-4:]}" if len(phone) >= 4 else "用户",
                password_hash=hash_password(str(uuid4())),
            )
            db.add(user)
            await db.commit()
        return await AppBffService.issue_tokens(user.id, redis_client, db, ip=client_ip)

    @staticmethod
    async def _revoke_family(db: AsyncSession, redis_client: redis.Redis, user_id: str, family_id: str, reason: str) -> None:
        sessions = (
            await db.execute(
                select(UserSession).where(UserSession.user_id == user_id, UserSession.session_family_id == family_id)
            )
        ).scalars().all()
        for s in sessions:
            s.status = "risk_locked"
            s.revoked_at = datetime.now(timezone.utc)
            s.revoke_reason = reason[:64]
            if s.current_refresh_jti:
                await redis_client.delete(f"rt:{s.current_refresh_jti}")
        await db.commit()

    @staticmethod
    async def refresh(refresh_token: str, redis_client: redis.Redis, db: AsyncSession, client_ip: str) -> dict[str, Any]:
        try:
            claims = decode_token(refresh_token, expected_type="refresh")
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=exc.message) from exc

        jti = claims.get("jti")
        user_id = str(claims.get("sub"))
        session_id = str(claims.get("sid") or "")
        family_id = str(claims.get("fid") or "")
        if not jti or not user_id or not session_id:
            raise AppError(code=AUTH_SESSION_REVOKED, message="refresh token invalid", http_status=401)

        session = (await db.execute(select(UserSession).where(UserSession.id == session_id))).scalar_one_or_none()
        if session is None or session.user_id != user_id or session.status != "active":
            raise AppError(code=AUTH_SESSION_REVOKED, message="refresh token revoked", http_status=401)

        if session.current_refresh_jti != jti:
            await AppBffService._log_auth_event(
                db,
                event_type="refresh_replay_detected",
                user_id=user_id,
                ip=client_ip,
                payload={"session_id": session_id, "jti": jti},
            )
            await AppBffService._revoke_family(db, redis_client, user_id, family_id or (session.session_family_id or ""), "replay")
            raise AppError(code=AUTH_TOKEN_REPLAY_DETECTED, message="refresh token replay detected", http_status=401)

        try:
            stored_user = await redis_client.get(f"rt:{jti}")
            if stored_user != user_id:
                raise AppError(code=AUTH_SESSION_REVOKED, message="refresh token revoked", http_status=401)
            await redis_client.delete(f"rt:{jti}")
            await redis_client.setex(f"rtu:{jti}", settings.REFRESH_TOKEN_TTL_SECONDS, session_id)
        except RedisError as exc:
            logger.error("refresh failed: redis unavailable user_id=%s err=%s", user_id, exc)
            raise AppError(code=REDIS_UNAVAILABLE, message="redis unavailable", http_status=503) from exc

        return await AppBffService.issue_tokens(
            user_id,
            redis_client,
            db,
            ip=client_ip,
            session_id=session_id,
        )

    @staticmethod
    async def logout(refresh_token: str, redis_client: redis.Redis, db: AsyncSession) -> dict[str, Any]:
        try:
            claims = decode_token(refresh_token, expected_type="refresh")
            jti = claims.get("jti")
            session_id = claims.get("sid")
            if jti:
                try:
                    await redis_client.delete(f"rt:{jti}")
                except RedisError as exc:
                    logger.error("logout failed: redis unavailable jti=%s err=%s", jti, exc)
                    raise AppError(code=REDIS_UNAVAILABLE, message="redis unavailable", http_status=503) from exc
            if session_id:
                s = (await db.execute(select(UserSession).where(UserSession.id == str(session_id)))).scalar_one_or_none()
                if s:
                    s.status = "revoked"
                    s.revoked_at = datetime.now(timezone.utc)
                    s.revoke_reason = "logout"
                    await db.commit()
        except AuthError:
            pass
        return {"logged_out": True}

    @staticmethod
    async def logout_all(user_id: str, redis_client: redis.Redis, db: AsyncSession) -> dict[str, Any]:
        sessions = (await db.execute(select(UserSession).where(UserSession.user_id == user_id))).scalars().all()
        now = datetime.now(timezone.utc)
        for s in sessions:
            s.status = "revoked"
            s.revoked_at = now
            s.revoke_reason = "logout_all"
            if s.current_refresh_jti:
                await redis_client.delete(f"rt:{s.current_refresh_jti}")
        await AppBffService._log_auth_event(
            db,
            event_type="logout_all",
            user_id=user_id,
            payload={"revoked": len(sessions)},
        )
        await db.commit()
        return {"revoked": len(sessions)}

    @staticmethod
    async def list_sessions(user_id: str, db: AsyncSession) -> dict[str, Any]:
        sessions = (
            await db.execute(
                select(UserSession).where(UserSession.user_id == user_id).order_by(desc(UserSession.created_at))
            )
        ).scalars().all()
        return {
            "items": [
                {
                    "id": s.id,
                    "status": s.status,
                    "device_info": s.device_info,
                    "ip": s.ip,
                    "last_ip": s.last_ip,
                    "last_seen_at": s.last_seen_at,
                    "created_at": s.created_at,
                    "revoked_at": s.revoked_at,
                    "revoke_reason": s.revoke_reason,
                }
                for s in sessions
            ]
        }

    @staticmethod
    async def list_auth_events(user_id: str, db: AsyncSession, *, limit: int = 50) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 100))
        rows = (
            await db.execute(
                select(AuthEvent)
                .where(AuthEvent.user_id == user_id)
                .order_by(desc(AuthEvent.created_at))
                .limit(safe_limit)
            )
        ).scalars().all()
        return {
            "items": [
                {
                    "id": row.id,
                    "event_type": row.event_type,
                    "ip": row.ip,
                    "user_agent": row.user_agent,
                    "payload": row.payload_json or {},
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        }

    @staticmethod
    async def revoke_session(user_id: str, session_id: str, redis_client: redis.Redis, db: AsyncSession) -> dict[str, Any]:
        session = (
            await db.execute(select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user_id))
        ).scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        session.status = "revoked"
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "manual"
        if session.current_refresh_jti:
            await redis_client.delete(f"rt:{session.current_refresh_jti}")
        await AppBffService._log_auth_event(
            db,
            event_type="session_revoke_manual",
            user_id=user_id,
            payload={"session_id": session_id},
        )
        await db.commit()
        return {"revoked": True, "session_id": session_id}

    @staticmethod
    async def change_password(
        user_id: str,
        old_password: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if not verify_password(old_password, user.password_hash):
            raise AppError(code=AUTH_INVALID_CREDENTIALS, message="invalid credentials", http_status=401)

        user.password_hash = hash_password(new_password)
        await db.commit()
        return {"updated": True}

    @staticmethod
    async def password_reset_request(account: str, redis_client: redis.Redis, db: AsyncSession) -> dict[str, Any]:
        AppBffService._ensure_account_channel_enabled(account)
        if "@" in account:
            user = (await db.execute(select(User).where(User.email == account))).scalar_one_or_none()
        else:
            user = (await db.execute(select(User).where(User.phone == account))).scalar_one_or_none()

        # keep response generic to avoid account enumeration
        if user is None:
            return {"sent": True}

        code = f"{random.randint(0, 999999):06d}"
        await redis_client.setex(
            f"otp:pwdreset:{account}",
            10 * 60,
            AppBffService._sha256(code),
        )
        await redis_client.setex(f"otp:pwdreset:attempts:{account}", 10 * 60, "0")
        send_resp = await AppBffService._send_code_by_account(account, code, "password_reset")
        return {"sent": True, **send_resp}

    @staticmethod
    async def password_reset_confirm(
        account: str,
        code: str,
        new_password: str,
        redis_client: redis.Redis,
        db: AsyncSession,
    ) -> dict[str, Any]:
        AppBffService._ensure_account_channel_enabled(account)
        code_hash = await redis_client.get(f"otp:pwdreset:{account}")
        if not code_hash:
            raise AppError(code=AUTH_RESET_CODE_INVALID, message="reset code invalid or expired", http_status=400)

        attempts_key = f"otp:pwdreset:attempts:{account}"
        attempts = await redis_client.incr(attempts_key)
        if attempts > 5:
            await redis_client.delete(f"otp:pwdreset:{account}")
            raise AppError(code=AUTH_RESET_CODE_INVALID, message="reset code invalid or expired", http_status=400)

        if AppBffService._sha256(code) != code_hash:
            raise AppError(code=AUTH_RESET_CODE_INVALID, message="reset code invalid or expired", http_status=400)

        if "@" in account:
            user = (await db.execute(select(User).where(User.email == account))).scalar_one_or_none()
        else:
            user = (await db.execute(select(User).where(User.phone == account))).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")

        user.password_hash = hash_password(new_password)

        sessions = (await db.execute(select(UserSession).where(UserSession.user_id == user.id))).scalars().all()
        for s in sessions:
            s.status = "revoked"
            s.revoked_at = datetime.now(timezone.utc)
            s.revoke_reason = "password_reset"
            if s.current_refresh_jti:
                await redis_client.delete(f"rt:{s.current_refresh_jti}")

        await redis_client.delete(f"otp:pwdreset:{account}")
        await redis_client.delete(attempts_key)
        await AppBffService._log_auth_event(
            db,
            event_type="password_reset_success",
            user_id=user.id,
            payload={"account": account},
        )
        await db.commit()
        return {"updated": True}

    @staticmethod
    async def oauth_start(provider: str, redis_client: redis.Redis) -> dict[str, Any]:
        if provider != "github":
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth provider unsupported", http_status=400)
        if not settings.GITHUB_OAUTH_CLIENT_ID or not settings.GITHUB_OAUTH_REDIRECT_URI:
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth provider not configured", http_status=400)

        state = str(uuid4())
        await redis_client.setex(f"oauth:state:github:{state}", 10 * 60, "1")
        auth_url = (
            "https://github.com/login/oauth/authorize"
            f"?client_id={settings.GITHUB_OAUTH_CLIENT_ID}"
            f"&redirect_uri={settings.GITHUB_OAUTH_REDIRECT_URI}"
            "&scope=read:user user:email"
            f"&state={state}"
        )
        return {"provider": "github", "state": state, "auth_url": auth_url}

    @staticmethod
    async def _oauth_fetch_github_user(code: str) -> dict[str, Any]:
        if not settings.GITHUB_OAUTH_CLIENT_ID or not settings.GITHUB_OAUTH_CLIENT_SECRET:
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth provider not configured", http_status=400)

        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                    "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth exchange failed", http_status=400)

            user_resp = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            user_resp.raise_for_status()
            user_data = user_resp.json()

        provider_uid = str(user_data.get("id") or "")
        if not provider_uid:
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth user invalid", http_status=400)
        return {
            "provider": "github",
            "provider_uid": provider_uid,
            "nickname": user_data.get("name") or user_data.get("login") or f"github_{provider_uid}",
            "email": user_data.get("email"),
            "access_token": access_token,
        }

    @staticmethod
    async def oauth_callback(
        provider: str,
        code: str,
        state: str,
        redis_client: redis.Redis,
        db: AsyncSession,
        client_ip: str,
    ) -> dict[str, Any]:
        if provider != "github":
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth provider unsupported", http_status=400)

        ok = await redis_client.get(f"oauth:state:github:{state}")
        if not ok:
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth state invalid", http_status=400)
        await redis_client.delete(f"oauth:state:github:{state}")

        profile = await AppBffService._oauth_fetch_github_user(code)
        provider_uid = profile["provider_uid"]

        oauth = (
            await db.execute(
                select(OAuthAccount).where(OAuthAccount.provider == provider, OAuthAccount.provider_uid == provider_uid)
            )
        ).scalar_one_or_none()

        is_new_user = False
        if oauth is None:
            user = None
            email = profile.get("email")
            if email:
                user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None:
                user = User(
                    id=str(uuid4()),
                    email=email,
                    phone=None,
                    nickname=str(profile.get("nickname") or "github_user")[:64],
                    password_hash=hash_password(str(uuid4())),
                )
                db.add(user)
                await db.flush()
                is_new_user = True

            oauth = OAuthAccount(
                id=str(uuid4()),
                user_id=user.id,
                provider=provider,
                provider_uid=provider_uid,
                access_token_enc=str(profile.get("access_token") or "")[:512] or None,
            )
            db.add(oauth)
            await db.commit()
            user_id = user.id
        else:
            user_id = oauth.user_id

        tokens = await AppBffService.issue_tokens(user_id, redis_client, db, ip=client_ip)
        tokens["oauth"] = {
            "provider": provider,
            "provider_uid": provider_uid,
            "email": profile.get("email"),
            "nickname": profile.get("nickname"),
            "is_new_user": is_new_user,
        }
        return tokens

    @staticmethod
    async def oauth_bind(
        user_id: str,
        provider: str,
        code: str,
        state: str,
        redis_client: redis.Redis,
        db: AsyncSession,
    ) -> dict[str, Any]:
        if provider != "github":
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth provider unsupported", http_status=400)
        ok = await redis_client.get(f"oauth:state:github:{state}")
        if not ok:
            raise AppError(code=AUTH_OAUTH_PROVIDER_UNSUPPORTED, message="oauth state invalid", http_status=400)
        await redis_client.delete(f"oauth:state:github:{state}")

        profile = await AppBffService._oauth_fetch_github_user(code)
        provider_uid = profile["provider_uid"]
        existing = (
            await db.execute(
                select(OAuthAccount).where(OAuthAccount.provider == provider, OAuthAccount.provider_uid == provider_uid)
            )
        ).scalar_one_or_none()
        if existing and existing.user_id != user_id:
            raise AppError(code=AUTH_OAUTH_BIND_CONFLICT, message="oauth account already bound", http_status=409)
        if existing is None:
            db.add(
                OAuthAccount(
                    id=str(uuid4()),
                    user_id=user_id,
                    provider=provider,
                    provider_uid=provider_uid,
                    access_token_enc=str(profile.get("access_token") or "")[:512] or None,
                )
            )
            await db.commit()
        return {"bound": True, "provider": provider}

    @staticmethod
    async def oauth_unbind(user_id: str, provider: str, db: AsyncSession) -> dict[str, Any]:
        oauth = (
            await db.execute(select(OAuthAccount).where(OAuthAccount.user_id == user_id, OAuthAccount.provider == provider))
        ).scalar_one_or_none()
        if oauth is None:
            return {"removed": False, "provider": provider}
        await db.delete(oauth)
        await db.commit()
        return {"removed": True, "provider": provider}

    @staticmethod
    def _auth_feature_config() -> dict[str, Any]:
        password_enabled = bool(settings.APP_AUTH_PASSWORD_ENABLED)
        register_enabled = bool(settings.APP_AUTH_REGISTER_ENABLED)
        otp_enabled = bool(settings.APP_AUTH_OTP_ENABLED)
        one_click_enabled = bool(settings.APP_AUTH_ONECLICK_ENABLED)
        github_enabled = bool(settings.APP_AUTH_GITHUB_OAUTH_ENABLED)
        password_reset_enabled = bool(settings.APP_AUTH_PASSWORD_RESET_ENABLED)
        phone_enabled = bool(settings.APP_AUTH_PHONE_ENABLED)
        email_enabled = bool(settings.APP_AUTH_EMAIL_ENABLED)

        checks = {
            "password_auth": {
                "enabled": password_enabled,
                "ready": password_enabled,
                "missing": [],
            },
            "register": {
                "enabled": register_enabled,
                "ready": register_enabled and password_enabled and (phone_enabled or email_enabled),
                "missing": ([] if (phone_enabled or email_enabled) else ["phone_or_email"]) if register_enabled else [],
            },
            "otp_auth": {
                "enabled": otp_enabled,
                "ready": otp_enabled and (phone_enabled or email_enabled),
                "missing": ([] if (phone_enabled or email_enabled) else ["phone_or_email"]) if otp_enabled else [],
            },
            "one_click": {
                "enabled": one_click_enabled,
                "ready": one_click_enabled and bool((settings.ONECLICK_PROVIDER or "").strip()),
                "missing": ([] if (settings.ONECLICK_PROVIDER or "").strip() else ["ONECLICK_PROVIDER"]) if one_click_enabled else [],
            },
            "password_reset": {
                "enabled": password_reset_enabled,
                "ready": password_reset_enabled and (phone_enabled or email_enabled),
                "missing": ([] if (phone_enabled or email_enabled) else ["phone_or_email"]) if password_reset_enabled else [],
            },
            "oauth_github": {
                "enabled": github_enabled,
                "ready": github_enabled and bool(settings.GITHUB_OAUTH_CLIENT_ID and settings.GITHUB_OAUTH_REDIRECT_URI),
                "missing": (
                    [
                        item
                        for item, ok in {
                            "GITHUB_OAUTH_CLIENT_ID": bool(settings.GITHUB_OAUTH_CLIENT_ID),
                            "GITHUB_OAUTH_REDIRECT_URI": bool(settings.GITHUB_OAUTH_REDIRECT_URI),
                        }.items()
                        if not ok
                    ]
                ) if github_enabled else [],
            },
        }
        ready = all((not item["enabled"]) or item["ready"] for item in checks.values())
        return {
            "ready": ready,
            "checks": checks,
            "public": {
                "password_login": password_enabled,
                "register": register_enabled,
                "otp_login": otp_enabled,
                "otp_register": otp_enabled and register_enabled,
                "password_reset": password_reset_enabled,
                "one_click": one_click_enabled,
                "oauth": {
                    "github": github_enabled,
                },
                "phone_enabled": phone_enabled,
                "email_enabled": email_enabled,
            },
        }

    @staticmethod
    def ensure_auth_feature_enabled(feature: str) -> None:
        checks = AppBffService._auth_feature_config()["checks"]
        item = checks.get(feature)
        if not item or not item.get("enabled"):
            raise HTTPException(status_code=404, detail="not found")

    @staticmethod
    async def auth_methods(user_id: str, db: AsyncSession) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        github_enabled = AppBffService._auth_feature_config()["checks"]["oauth_github"]["enabled"]
        github_bound = False
        oauth_providers: list[str] = []
        if github_enabled:
            oauth = (
                await db.execute(
                    select(OAuthAccount).where(OAuthAccount.user_id == user_id, OAuthAccount.provider == "github")
                )
            ).scalar_one_or_none()
            github_bound = oauth is not None
            if github_bound:
                oauth_providers.append("github")
        return {
            "email_bound": bool(user.email),
            "phone_bound": bool(user.phone),
            "oauth_providers": oauth_providers,
            "github_bound": github_bound,
            "phone_enabled": bool(settings.APP_AUTH_PHONE_ENABLED),
            "email_enabled": bool(settings.APP_AUTH_EMAIL_ENABLED),
            "oauth_enabled": {
                "github": bool(github_enabled),
            },
        }

    @staticmethod
    async def auth_config_check() -> dict[str, Any]:
        config = AppBffService._auth_feature_config()
        return {"ready": config["ready"], "checks": config["checks"]}

    @staticmethod
    async def public_auth_config() -> dict[str, Any]:
        config = AppBffService._auth_feature_config()
        return {
            "ready": config["ready"],
            "auth": config["public"],
            "checks": config["checks"],
        }

    @staticmethod
    async def _get_user(user_id: str, db: AsyncSession) -> User:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        return user

    @staticmethod
    async def _get_or_create_profile(user_id: str, db: AsyncSession) -> UserProfile:
        result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = UserProfile(user_id=user_id)
            db.add(profile)
            await db.commit()
            await db.refresh(profile)
        return profile

    @staticmethod
    async def _get_or_create_preferences(user_id: str, db: AsyncSession) -> UserPreference:
        result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        pref = result.scalar_one_or_none()
        if pref is None:
            pref = UserPreference(
                user_id=user_id,
                taste_tags=[],
                avoid_ingredients=[],
                allergens=[],
            )
            db.add(pref)
            await db.commit()
            await db.refresh(pref)
        return pref

    @staticmethod
    async def get_me(user_id: str, db: AsyncSession) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        return map_me(user, profile, pref)

    @staticmethod
    async def update_me(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)

        if payload.get("name") is not None:
            user.nickname = payload["name"]
        if payload.get("avatar") is not None:
            user.avatar_url = payload["avatar"]

        if payload.get("health_goal") is not None:
            profile.health_goal = payload["health_goal"]
        if payload.get("current_state") is not None:
            profile.current_state = payload["current_state"]

        await db.commit()
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        return map_me(user, profile, pref)

    @staticmethod
    async def update_goal_state(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        profile = await AppBffService._get_or_create_profile(user_id, db)

        if "health_goal" in payload:
            profile.health_goal = payload["health_goal"]
        if "current_state" in payload:
            profile.current_state = payload["current_state"]

        await db.commit()
        return {
            "health_goal": profile.health_goal,
            "current_state": profile.current_state,
        }

    @staticmethod
    async def get_home_overview(
        user_id: str,
        request_client_ip: str,
        db: AsyncSession,
        location: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)

        default_city = "北京"
        weather_query_city = default_city
        weather_display_city = default_city
        resolved_location = location

        if resolved_location:
            regeo_region = await amap.reverse_geocode_region(
                resolved_location,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
            if isinstance(regeo_region, dict):
                district = regeo_region.get("district")
                city = regeo_region.get("city")
                province = regeo_region.get("province")
                if isinstance(district, str) and district.strip():
                    weather_display_city = district.strip()
                elif isinstance(city, str) and city.strip():
                    weather_display_city = city.strip()
                elif isinstance(province, str) and province.strip():
                    weather_display_city = province.strip()

                if isinstance(city, str) and city.strip():
                    weather_query_city = city.strip()
                elif isinstance(province, str) and province.strip():
                    weather_query_city = province.strip()
        elif request_client_ip not in {"unknown", "testclient", "test", "localhost", "127.0.0.1", "::1"}:
            ip_location, ip_city = await amap.get_ip_location(
                request_client_ip,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
            if ip_location:
                resolved_location = ip_location
            if isinstance(ip_city, str) and ip_city.strip():
                value = ip_city.strip()
                weather_query_city = value
                weather_display_city = value

        weather = await amap.get_weather(weather_query_city, servers_path=settings.MCP_SERVERS_CONFIG_PATH)
        temperature = weather.get("temperature_c") if isinstance(weather, dict) else None

        if isinstance(temperature, (int, float)):
            temperature_text = f"{int(round(float(temperature)))}°"
        else:
            temperature_text = "--°"

        weather_status = ""
        if isinstance(weather, dict):
            status = weather.get("status")
            weather_status = str(status) if isinstance(status, str) else ""

        return {
            "name": user.nickname,
            "health_goal": profile.health_goal,
            "current_state": profile.current_state,
            "weather": {
                "city": weather_display_city,
                "temperature_c": temperature,
                "status": weather_status,
                "temperature_text": temperature_text,
                "display": f"{temperature_text}{weather_status}" if weather_status else temperature_text,
                "location": resolved_location,
            },
        }

    @staticmethod
    async def get_preferences(user_id: str, db: AsyncSession) -> dict[str, Any]:
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        return map_preferences(pref)

    @staticmethod
    async def update_preferences(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        if payload.get("tastes") is not None:
            pref.taste_tags = payload["tastes"]
        if payload.get("taboos") is not None:
            pref.avoid_ingredients = payload["taboos"]
        if payload.get("allergens") is not None:
            pref.allergens = payload["allergens"]
        if payload.get("spicy_level") is not None:
            pref.spicy_level = payload["spicy_level"]
        if payload.get("budget_level") is not None:
            pref.budget_level = payload["budget_level"]

        await db.commit()

        pattern = f"context:user:{user_id}:*"
        keys = []
        async for key in redis_client.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            await redis_client.delete(*keys)

        return map_preferences(pref)

    @staticmethod
    async def list_ingredients(user_id: str, db: AsyncSession) -> list[dict[str, Any]]:
        result = await db.execute(
            select(FridgeItem)
            .where(FridgeItem.user_id == user_id)
            .order_by(desc(FridgeItem.updated_at))
        )
        items = result.scalars().all()
        return [map_ingredient(item) for item in items]

    @staticmethod
    async def get_expiring_ingredients(
        user_id: str,
        db: AsyncSession,
        *,
        within_days: int = 3,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=max(1, min(within_days, 14)))
        result = await db.execute(
            select(FridgeItem)
            .where(FridgeItem.user_id == user_id, FridgeItem.expiry_date.is_not(None))
            .order_by(FridgeItem.expiry_date.asc())
        )
        items = result.scalars().all()

        data: list[dict[str, Any]] = []
        for item in items:
            expiry = item.expiry_date
            if expiry is None:
                continue
            expiry_at = expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
            if expiry_at > horizon:
                continue
            delta = expiry_at - now
            hours_left = int(delta.total_seconds() // 3600)
            if delta.total_seconds() < 0:
                status = "expired"
            elif hours_left <= 24:
                status = "expiring_24h"
            else:
                status = "expiring_72h"

            row = map_ingredient(item)
            row.update(
                {
                    "status": status,
                    "hours_left": hours_left,
                }
            )
            data.append(row)
        return data

    @staticmethod
    async def build_clear_inventory_plan(
        user_id: str,
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        expiring = await AppBffService.get_expiring_ingredients(user_id, db, within_days=3)
        if not expiring:
            result = await db.execute(
                select(FridgeItem)
                .where(FridgeItem.user_id == user_id)
                .order_by(desc(FridgeItem.updated_at))
                .limit(3)
            )
            fallback_items = result.scalars().all()
            names = [item.name for item in fallback_items]
        else:
            names = [item.get("name") for item in expiring[:3] if item.get("name")]

        query = " ".join(names).strip() or "快手菜"
        recipes = await RecipeService.search(redis_client, query)
        ingredient_names = names[:]
        return {
            "priority_items": expiring[:5],
            "query": query,
            "recipes": [map_home_chef_recipe(item, ingredient_names) for item in recipes[:3]],
        }

    @staticmethod
    async def create_grocery_list_from_recipe(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        recipe_name = str(payload.get("recipe_name") or "").strip() or "食材准备清单"
        required_items = payload.get("required_items") or []

        title = recipe_name if recipe_name.endswith("食材准备清单") else f"{recipe_name} 食材准备清单"

        result = await db.execute(select(FridgeItem).where(FridgeItem.user_id == user_id))
        fridge_items = result.scalars().all()
        fridge_map = {item.name.lower(): item for item in fridge_items}

        list_obj = GroceryList(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            source_recipe=recipe_name,
        )
        db.add(list_obj)

        added_items: list[dict[str, Any]] = []
        for raw in required_items:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            need_qty = raw.get("quantity")
            unit = raw.get("unit")
            category = raw.get("category")

            fridge_item = fridge_map.get(name.lower())
            missing = True
            if fridge_item is not None:
                if need_qty is None or fridge_item.quantity is None:
                    missing = False
                else:
                    try:
                        missing = float(fridge_item.quantity) < float(need_qty)
                    except Exception:
                        missing = False

            if not missing:
                continue

            item_obj = GroceryListItem(
                id=str(uuid4()),
                list_id=list_obj.id,
                name=name,
                quantity=need_qty,
                unit=unit,
                category=category,
                checked=False,
            )
            db.add(item_obj)
            added_items.append(
                {
                    "id": item_obj.id,
                    "name": item_obj.name,
                    "quantity": item_obj.quantity,
                    "unit": item_obj.unit,
                    "category": item_obj.category,
                    "checked": item_obj.checked,
                }
            )

        await db.commit()
        return {
            "id": list_obj.id,
            "title": list_obj.title,
            "source_recipe": list_obj.source_recipe,
            "items": added_items,
        }

    @staticmethod
    async def get_grocery_list(user_id: str, list_id: str, db: AsyncSession) -> dict[str, Any]:
        list_result = await db.execute(
            select(GroceryList).where(GroceryList.id == list_id, GroceryList.user_id == user_id)
        )
        list_obj = list_result.scalar_one_or_none()
        if list_obj is None:
            raise HTTPException(status_code=404, detail="grocery list not found")

        items_result = await db.execute(
            select(GroceryListItem)
            .where(GroceryListItem.list_id == list_obj.id)
            .order_by(GroceryListItem.created_at.asc())
        )
        items = items_result.scalars().all()
        return {
            "id": list_obj.id,
            "title": list_obj.title,
            "source_recipe": list_obj.source_recipe,
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "category": item.category,
                    "checked": item.checked,
                }
                for item in items
            ],
        }

    @staticmethod
    async def toggle_grocery_item(
        user_id: str,
        list_id: str,
        item_id: str,
        checked: bool,
        db: AsyncSession,
    ) -> dict[str, Any]:
        list_result = await db.execute(
            select(GroceryList).where(GroceryList.id == list_id, GroceryList.user_id == user_id)
        )
        list_obj = list_result.scalar_one_or_none()
        if list_obj is None:
            raise HTTPException(status_code=404, detail="grocery list not found")

        item_result = await db.execute(
            select(GroceryListItem).where(
                GroceryListItem.id == item_id,
                GroceryListItem.list_id == list_id,
            )
        )
        item = item_result.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="grocery item not found")

        item.checked = bool(checked)
        await db.commit()
        await db.refresh(item)
        return {
            "id": item.id,
            "name": item.name,
            "checked": item.checked,
        }

    @staticmethod
    async def create_ingredient(user_id: str, payload: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
        item = FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name=payload["name"],
            quantity=payload.get("quantity"),
            unit=payload.get("unit"),
            expiry_date=payload.get("expiry_date"),
            source=payload.get("source") or "manual",
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return map_ingredient(item)

    @staticmethod
    async def update_ingredient(
        user_id: str,
        ingredient_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        result = await db.execute(
            select(FridgeItem).where(FridgeItem.id == ingredient_id, FridgeItem.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        for field, value in payload.items():
            setattr(item, field, value)
        await db.commit()
        await db.refresh(item)
        return map_ingredient(item)

    @staticmethod
    async def delete_ingredient(user_id: str, ingredient_id: str, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(FridgeItem).where(FridgeItem.id == ingredient_id, FridgeItem.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        await db.delete(item)
        await db.commit()
        return {"deleted": True}

    @staticmethod
    async def create_scan_job(
        user_id: str,
        object_key: str,
        captured_at: datetime | None,
        db: AsyncSession,
    ) -> dict[str, Any]:
        photo = FridgePhoto(
            id=str(uuid4()),
            user_id=user_id,
            object_key=object_key,
            captured_at=captured_at,
        )
        db.add(photo)

        job = RecognitionJob(
            id=str(uuid4()),
            user_id=user_id,
            photo_id=photo.id,
            status="queued",
            result_json=None,
            error=None,
        )
        db.add(job)
        await db.commit()
        return {"scan_id": job.id, "status": job.status, "photo_id": photo.id}

    @staticmethod
    async def get_scan(user_id: str, scan_id: str, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(RecognitionJob).where(RecognitionJob.id == scan_id, RecognitionJob.user_id == user_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="scan not found")

        return {
            "scan_id": job.id,
            "status": job.status,
            "result": job.result_json,
            "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

    @staticmethod
    async def complete_scan_job_inline(scan_id: str, db: AsyncSession) -> None:
        result = await db.execute(select(RecognitionJob).where(RecognitionJob.id == scan_id))
        job = result.scalar_one_or_none()
        if job is None or job.status not in {"queued", "running"}:
            return

        job.status = "running"
        await db.commit()

        job.result_json = {
            "items": [
                {"name": "egg", "quantity": 2, "unit": "pcs"},
                {"name": "tomato", "quantity": 3, "unit": "pcs"},
            ],
            "request_id": str(uuid4()),
        }
        job.status = "success"
        job.finished_at = datetime.now(timezone.utc)
        await db.commit()

    @staticmethod
    async def apply_scan(user_id: str, scan_id: str, merge_by_name: bool, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(RecognitionJob).where(RecognitionJob.id == scan_id, RecognitionJob.user_id == user_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="scan not found")
        if job.status != "success" or not isinstance(job.result_json, dict):
            raise HTTPException(status_code=400, detail="scan not ready")

        raw_items = job.result_json.get("items")
        if not isinstance(raw_items, list):
            raw_items = []

        applied: list[FridgeItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            name = str(raw["name"])
            quantity = raw.get("quantity")
            unit = raw.get("unit")

            existing = None
            if merge_by_name:
                existing_result = await db.execute(
                    select(FridgeItem).where(
                        FridgeItem.user_id == user_id,
                        FridgeItem.name == name,
                        FridgeItem.unit == unit,
                    )
                )
                existing = existing_result.scalar_one_or_none()

            if existing:
                if isinstance(quantity, (int, float)):
                    current = existing.quantity or 0
                    existing.quantity = current + float(quantity)
                applied.append(existing)
            else:
                item = FridgeItem(
                    id=str(uuid4()),
                    user_id=user_id,
                    name=name,
                    quantity=quantity if isinstance(quantity, (int, float)) else None,
                    unit=unit if isinstance(unit, str) else None,
                    source="recognition",
                )
                db.add(item)
                applied.append(item)

        await db.commit()

        response_items = [map_ingredient(item) for item in applied]
        return {
            "scan_id": scan_id,
            "applied_count": len(response_items),
            "items": response_items,
        }

    @staticmethod
    async def generate_home_chef_recipes(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        ingredient_names = payload.get("ingredients") or []
        if not ingredient_names:
            result = await db.execute(select(FridgeItem).where(FridgeItem.user_id == user_id).limit(10))
            ingredient_names = [item.name for item in result.scalars().all()]

        ingredient_names = [name for name in ingredient_names if isinstance(name, str) and name.strip()]
        query = " ".join(ingredient_names[:4]) if ingredient_names else "home"
        count = max(1, min(int(payload.get("count", 2)), 5))

        # 1) 优先用 LLM 直接生成菜谱+具体做法
        recipes = await AppBffService._generate_home_chef_recipes_with_llm(ingredient_names, count)

        # 2) LLM 不可用时，退回本地检索+模板步骤
        if not recipes:
            try:
                raw_recipes = await RecipeService.search(redis_client, query)
                recipes = [map_home_chef_recipe(item, ingredient_names) for item in raw_recipes[:count]]
            except Exception:
                recipes = []

        # 3) 最终兜底
        if not recipes:
            recipes = [
                {
                    "title": "家常西红柿炒鸡蛋",
                    "desc": "经典酸甜下饭",
                    "time": "10min",
                    "cal": "180kcal",
                    "img": "cooking_dish",
                    "tag": "高蛋白",
                    "ingredients": ["鸡蛋 3个", "西红柿 2个", "小葱 1根"],
                    "steps": ["西红柿切块，鸡蛋打散", "先炒鸡蛋盛出，再炒西红柿", "回锅翻炒并调味"],
                    "method_markdown": "### 食材准备\n- 鸡蛋 3个\n- 西红柿 2个\n- 小葱 1根\n\n### 做法步骤\n1. 鸡蛋加少许盐打散，热油快炒至七八分熟盛出。\n2. 西红柿切块下锅炒软，加入少许糖提鲜。\n3. 倒回鸡蛋快速翻炒，让蛋吸收番茄汁。\n4. 撒葱花翻匀即可出锅。\n\n### 小贴士\n- 蛋液加一点水更嫩。\n- 番茄汁偏稀可小火多收一会儿。",
                },
                {
                    "title": "青椒炒肉丝",
                    "desc": "下饭快手菜",
                    "time": "15min",
                    "cal": "260kcal",
                    "img": "cooking_dish",
                    "tag": "家常",
                    "ingredients": ["猪肉 150g", "青椒 2个", "姜蒜 适量"],
                    "steps": ["肉丝腌制 10 分钟", "青椒切丝备用", "先炒肉再合炒青椒"],
                    "method_markdown": "### 食材准备\n- 猪肉 150g\n- 青椒 2个\n- 姜蒜 适量\n\n### 做法步骤\n1. 肉丝加生抽和淀粉抓匀腌 10 分钟。\n2. 青椒切丝，姜蒜切末。\n3. 先把肉丝滑炒至变色盛出。\n4. 爆香姜蒜后下青椒，再回锅肉丝调味翻炒。\n\n### 小贴士\n- 肉丝不要炒太久，避免发柴。",
                },
            ][:count]

        return {"recipes": recipes}

    @staticmethod
    async def _generate_home_chef_recipes_with_llm(
        ingredient_names: list[str],
        count: int,
    ) -> list[dict[str, Any]]:
        try:
            provider = ProviderRegistry.get(settings.LLM_PROVIDER)
            if not provider.api_key:
                return []

            client = AsyncOpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                max_retries=1,
                timeout=httpx.Timeout(40.0, connect=5.0),
            )

            prompt = (
                "# Role\n"
                "你是一位拥有20年经验的米其林三星主厨，擅长把复杂烹饪科学转化为家庭可执行指南。\n\n"
                "# Task\n"
                "请根据给定食材生成专业级家常菜谱。\n\n"
                "# Constraints\n"
                "1) 精确量化：禁止'适量/少许'，必须给出具体克数(g)/毫升(ml)/勺(tsp/Tbsp)。\n"
                "2) 逻辑完整：必须覆盖前置准备、正式烹饪、收尾与摆盘建议。\n"
                "3) 关键细节：关键步骤注明火候(大火/中火/小火)和预期状态(例如：表面微微起泡、颜色转金黄)。\n"
                "4) 专业解释：给出2-3条核心成功秘诀 + 常见翻车点及补救。\n\n"
                "# Output\n"
                "严格输出 JSON 数组，不要 markdown 代码块、不要解释。\n"
                "每个对象必须包含字段: title, desc, time, cal, img, tag, ingredients, steps, method_markdown。\n"
                "- ingredients: 字符串数组（带精确数量）\n"
                "- steps: 字符串数组（至少6步，包含火候/状态）\n"
                "- method_markdown: 中文 Markdown，且必须包含以下小节：\n"
                "  ## 1. 菜名与风味简介\n"
                "  ## 2. 食材清单\n"
                "  ## 3. 详细烹饪步骤\n"
                "  ## 4. 主厨的“独门绝技”\n"
                "  ## 5. 常见翻车避雷指南\n"
                "img 固定为 cooking_dish。"
            )
            user_text = json.dumps(
                {
                    "ingredients": ingredient_names,
                    "count": count,
                    "constraints": ["优先使用现有食材", "步骤清晰可执行"],
                },
                ensure_ascii=False,
            )

            resp = await client.chat.completions.create(
                model=provider.model_writer,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.6,
            )
            content = (resp.choices[0].message.content or "").strip()
            parsed = AppBffService._parse_recipe_json(content)
            return AppBffService._normalize_llm_recipes(parsed, count)
        except Exception as exc:
            logger.info("home_chef_llm_fallback reason=%s", str(exc))
            return []

    @staticmethod
    def _parse_recipe_json(content: str) -> list[dict[str, Any]]:
        if not content:
            return []
        text = content.strip().replace("```json", "").replace("```", "").strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                data = json.loads(text)
                return data if isinstance(data, list) else []
            except Exception:
                pass

        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, list) else []
            except Exception:
                return []
        return []

    @staticmethod
    def _normalize_llm_recipes(raw: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            ingredients = item.get("ingredients") if isinstance(item.get("ingredients"), list) else []
            steps = item.get("steps") if isinstance(item.get("steps"), list) else []
            if len(steps) < 6:
                continue
            norm_ingredients = [str(x) for x in ingredients if isinstance(x, str)][:12]
            norm_steps = [str(x) for x in steps if isinstance(x, str)][:12]
            method_markdown = str(item.get("method_markdown") or "").strip()
            if not method_markdown:
                method_markdown = "\n".join([
                    "## 1. 菜名与风味简介",
                    f"{title}，家常风味，适合工作日晚餐。",
                    "",
                    "## 2. 食材清单",
                    "### 主料",
                    *[f"- {x}" for x in norm_ingredients],
                    "### 辅料/调料",
                    "- 食用油 15ml",
                    "- 盐 2g",
                    "",
                    "## 3. 详细烹饪步骤",
                    *[f"{idx + 1}. {x}" for idx, x in enumerate(norm_steps)],
                    "",
                    "## 4. 主厨的“独门绝技”",
                    "- 关键蛋白食材先高温定型后回锅，口感更嫩。",
                    "- 汤汁阶段用中小火收汁，风味更集中。",
                    "",
                    "## 5. 常见翻车避雷指南",
                    "- 火太大易焦糊：转中火并补 10ml 清水。",
                    "- 口味偏淡：分两次补盐，每次 1g。",
                ])

            results.append(
                {
                    "title": title,
                    "desc": str(item.get("desc") or "家常快手菜")[:20],
                    "time": str(item.get("time") or "15min"),
                    "cal": str(item.get("cal") or "250kcal"),
                    "img": "cooking_dish",
                    "tag": str(item.get("tag") or "家常")[:12],
                    "ingredients": norm_ingredients,
                    "steps": norm_steps,
                    "method_markdown": method_markdown,
                }
            )
            if len(results) >= count:
                break
        return results

    @staticmethod
    async def get_today_card(user_id: str, db: AsyncSession) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)
        now = datetime.now()
        return {
            "name": user.nickname,
            "health_goal": profile.health_goal,
            "current_state": profile.current_state,
            "weather": {"temp_c": 26, "text": "晴"},
            "time_of_day": now.strftime("%H:%M"),
            "weekday": now.strftime("%A"),
        }

    @staticmethod
    def _fallback_restaurants(lat: float | None, lng: float | None) -> list[dict[str, Any]]:
        base = [
            {"provider": "amap", "provider_id": "mock_1", "name": "老上海本帮菜", "rating": 4.8, "price": 88, "tags": ["剁椒鱼头必点"], "geo": {"lat": lat, "lng": lng}, "source": "fallback_mock"},
            {"provider": "amap", "provider_id": "mock_2", "name": "深夜拉面馆", "rating": 4.7, "price": 42, "tags": ["汤底浓郁"], "geo": {"lat": lat, "lng": lng}, "source": "fallback_mock"},
            {"provider": "amap", "provider_id": "mock_3", "name": "轻食能量碗", "rating": 4.6, "price": 36, "tags": ["减脂推荐"], "geo": {"lat": lat, "lng": lng}, "source": "fallback_mock"},
        ]
        return base

    @staticmethod
    def _extract_price_value(price_text: Any) -> float | None:
        if isinstance(price_text, (int, float)):
            value = float(price_text)
            return value if value > 0 else None
        if not isinstance(price_text, str):
            return None
        numeric = "".join(ch for ch in price_text if ch.isdigit() or ch == ".")
        if not numeric:
            return None
        try:
            value = float(numeric)
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _sort_restaurants(rows: list[dict[str, Any]], sort: str | None) -> list[dict[str, Any]]:
        sort_key = (sort or "").strip().lower()
        if sort_key in {"nearest", "distance", "distance_asc"}:
            return sorted(rows, key=lambda row: (row.get("distance_m") is None, row.get("distance_m") or 0))
        if sort_key in {"rating_desc", "rating", "score_desc"}:
            return sorted(
                rows,
                key=lambda row: (
                    row.get("rating") is None,
                    -(float(row.get("rating"))) if isinstance(row.get("rating"), (int, float)) else 0,
                ),
            )
        if sort_key in {"price_asc", "price", "cost_asc"}:
            return sorted(
                rows,
                key=lambda row: (
                    AppBffService._extract_price_value(row.get("price_text")) is None,
                    AppBffService._extract_price_value(row.get("price_text")) or 0,
                ),
            )
        return rows

    @staticmethod
    async def list_restaurants(
        redis_client: redis.Redis,
        q: str | None,
        tag: str | None,
        lat: float | None,
        lng: float | None,
        sort: str | None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        service_failed = False
        try:
            results = await RestaurantService.search(redis_client, q, tag, lat, lng, sort)
        except Exception:
            service_failed = True
            results = []

        if service_failed and not results and settings.APP_FALLBACK_ENABLED:
            results = AppBffService._fallback_restaurants(lat, lng)

        mapped_rows = [map_restaurant(item, lat, lng) for item in results]
        return AppBffService._sort_restaurants(mapped_rows, sort)

    @staticmethod
    async def restaurant_detail(
        provider: str,
        provider_id: str,
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        detail = None
        service_failed = False
        try:
            detail = await RestaurantService.get_detail(db, redis_client, provider, provider_id)
        except Exception:
            service_failed = True
            detail = None

        if service_failed and not detail and settings.APP_FALLBACK_ENABLED:
            detail = {
                "provider": provider,
                "provider_id": provider_id,
                "name": "餐厅信息加载中",
                "rating": 4.6,
                "price": 58,
                "tags": ["fallback_mock"],
                "geo": None,
                "source": "fallback_mock",
            }

        if not detail:
            raise HTTPException(status_code=404, detail="restaurant not found")

        mapped = map_restaurant(detail, None, None)
        mapped["raw"] = detail.get("raw")
        return mapped

    @staticmethod
    async def blind_box_draw(user_id: str, seed: str | None, db: AsyncSession) -> dict[str, Any]:
        pool = ["noodles", "dumplings", "salad", "soup", "rice bowl"]
        pref_result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        pref = pref_result.scalar_one_or_none()
        avoid = set((pref.avoid_ingredients or []) + (pref.allergens or [])) if pref else set()
        filtered = [item for item in pool if item not in avoid]
        warnings: list[str] = []
        if filtered:
            pool = filtered
        else:
            warnings.append("avoid list filtered all items; fallback to full pool")

        actual_seed = seed or str(uuid4())
        rng = random.Random(actual_seed)
        picked = rng.choice(pool)

        roll = BlindboxRoll(
            id=str(uuid4()),
            user_id=user_id,
            result=picked,
            seed=actual_seed,
        )
        db.add(roll)
        await db.commit()

        return {
            "result": map_blindbox_result(picked),
            "seed": actual_seed,
            "warnings": warnings,
        }

    @staticmethod
    async def _get_latest_wheel(user_id: str, db: AsyncSession) -> WheelConfig | None:
        result = await db.execute(
            select(WheelConfig)
            .where(WheelConfig.user_id == user_id)
            .order_by(desc(WheelConfig.updated_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _default_wheel_options() -> list[str]:
        return ["火锅", "寿司", "汉堡", "拉面", "麻辣烫", "沙拉"]

    @staticmethod
    def _extract_labels(config: WheelConfig | None) -> list[str]:
        if not config:
            return AppBffService._default_wheel_options()
        raw = config.options
        if isinstance(raw, dict):
            options = raw.get("options")
            if isinstance(options, list):
                labels = []
                for opt in options:
                    if isinstance(opt, dict) and isinstance(opt.get("label"), str):
                        labels.append(opt["label"])
                if labels:
                    return labels
        return AppBffService._default_wheel_options()

    @staticmethod
    async def get_wheel_current(user_id: str, db: AsyncSession) -> dict[str, Any]:
        config = await AppBffService._get_latest_wheel(user_id, db)
        return {
            "wheel_id": config.id if config else None,
            "name": config.name if config else "我的转盘",
            "options": AppBffService._extract_labels(config),
        }

    @staticmethod
    async def upsert_wheel_current(user_id: str, payload: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
        labels = [label.strip() for label in payload.get("options", []) if isinstance(label, str) and label.strip()]
        if len(labels) < 2:
            raise HTTPException(status_code=400, detail="wheel requires at least two options")

        config = await AppBffService._get_latest_wheel(user_id, db)
        options = {"options": [{"label": label} for label in labels]}

        if config is None:
            config = WheelConfig(
                id=str(uuid4()),
                user_id=user_id,
                name=payload.get("name") or "我的转盘",
                options=options,
            )
            db.add(config)
        else:
            config.name = payload.get("name") or config.name
            config.options = options

        await db.commit()
        await db.refresh(config)
        return {
            "wheel_id": config.id,
            "name": config.name,
            "options": labels,
        }

    @staticmethod
    async def spin_wheel_current(user_id: str, seed: str | None, db: AsyncSession) -> dict[str, Any]:
        config = await AppBffService._get_latest_wheel(user_id, db)
        if config is None:
            config = WheelConfig(
                id=str(uuid4()),
                user_id=user_id,
                name="我的转盘",
                options={"options": [{"label": label} for label in AppBffService._default_wheel_options()]},
            )
            db.add(config)
            await db.commit()
            await db.refresh(config)

        options = AppBffService._extract_labels(config)

        pref_result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        pref = pref_result.scalar_one_or_none()
        avoid = set((pref.avoid_ingredients or []) + (pref.allergens or [])) if pref else set()

        def allowed(label: str) -> bool:
            lower = label.lower()
            return all(term.lower() not in lower for term in avoid)

        filtered = [label for label in options if allowed(label)]
        warnings: list[str] = []
        if filtered:
            options = filtered
        else:
            warnings.append("avoid list filtered all options; fallback to full options")

        actual_seed = seed or str(uuid4())
        rng = random.Random(actual_seed)
        winner = rng.choice(options)
        angle = rng.random() * 360

        spin = WheelSpin(
            id=str(uuid4()),
            user_id=user_id,
            config_id=config.id,
            result=winner,
            seed=actual_seed,
            angle=angle,
        )
        db.add(spin)
        await db.commit()

        return {
            "wheel_id": config.id,
            "winner": winner,
            "angle": angle,
            "seed": actual_seed,
            "warnings": warnings,
        }

    @staticmethod
    async def create_chat_session(
        user_id: str | None,
        db: AsyncSession,
        *,
        scene: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        resolved_scene = (scene or "chat").strip() or "chat"
        resolved_title = (title or "新会话").strip() or "新会话"
        session = ChatSession(
            id=str(uuid4()),
            user_id=user_id,
            scene=resolved_scene,
            title=resolved_title,
        )
        db.add(session)
        await db.commit()
        return {"session_id": session.id, "scene": session.scene, "title": session.title}

    @staticmethod
    async def create_chat_attachment(
        user_id: str,
        session_id: str,
        filename: str | None,
        content_type: str | None,
        content: bytes,
        minio: Any,
    ) -> dict[str, Any]:
        if not content:
            raise HTTPException(status_code=400, detail="empty attachment")
        if len(content) > settings.CHAT_ATTACHMENT_MAX_BYTES:
            raise HTTPException(status_code=413, detail="attachment too large")

        resolved_content_type = (content_type or "").strip().lower()
        if not resolved_content_type.startswith("image/"):
            raise HTTPException(status_code=415, detail="only image attachments are supported")

        raw_name = (filename or "image").strip() or "image"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._") or "image"
        attachment_id = str(uuid4())
        object_key = f"chat/{user_id}/{session_id}/{attachment_id}_{safe_name}"
        await minio.upload_bytes(object_key, content)

        return {
            "attachment_id": attachment_id,
            "kind": "image",
            "object_key": object_key,
            "filename": raw_name,
            "content_type": resolved_content_type,
            "size_bytes": len(content),
        }

    @staticmethod
    async def rename_chat_session(
        user_id: str | None,
        session_id: str,
        title: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        session.title = title
        await db.commit()
        return {"updated": True, "title": session.title}

    @staticmethod
    async def delete_chat_session(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        session.deleted_at = datetime.utcnow()
        await db.commit()

        await conversation.clear_session_cache(redis_client, session_id)

        return {"deleted": True}

    @staticmethod
    async def list_chat_sessions(
        user_id: str | None,
        db: AsyncSession,
        limit: int,
        offset: int,
        q: str | None,
        scene: str | None = None,
    ) -> dict[str, Any]:
        stmt = (
            select(ChatSession)
            .where(ChatSession.deleted_at.is_(None))
            .order_by(desc(ChatSession.created_at))
            .offset(offset)
            .limit(limit)
        )
        if user_id:
            stmt = stmt.where(ChatSession.user_id == user_id)
        if q:
            stmt = stmt.where(ChatSession.title.contains(q))
        if scene:
            stmt = stmt.where(ChatSession.scene == scene)

        rows = (await db.execute(stmt)).scalars().all()
        updated = False
        for row in rows:
            if not row.title or row.title == "新会话":
                msg_stmt = (
                    select(ChatMessage)
                    .where(ChatMessage.session_id == row.id, ChatMessage.role == "user")
                    .order_by(ChatMessage.created_at)
                    .limit(1)
                )
                msg_result = await db.execute(msg_stmt)
                msg = msg_result.scalar_one_or_none()
                if msg and msg.content:
                    title = msg.content.strip().replace("\n", " ")
                    row.title = title[:24] if len(title) > 24 else title
                    updated = True
        if updated:
            await db.commit()

        tz = timezone(timedelta(hours=8))
        sessions = []
        for row in rows:
            created_at = row.created_at
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            sessions.append(
                {
                    "session_id": row.id,
                    "scene": row.scene,
                    "title": row.title,
                    "created_at": created_at.astimezone(tz).isoformat() if created_at else None,
                }
            )
        return {"sessions": sessions, "offset": offset, "limit": limit}

    @staticmethod
    async def list_chat_messages(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        rows = (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()

        messages = [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "tool_name": row.tool_name,
                "tool_payload": row.tool_payload_json,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
        return {"messages": messages, "offset": offset, "limit": limit}

    @staticmethod
    async def ensure_chat_session_access(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        allow_missing: bool = True,
    ) -> None:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            if allow_missing:
                return
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

    @staticmethod
    async def stop_chat(session_id: str, redis_client: redis.Redis) -> dict[str, Any]:
        await redis_client.setex(f"chat:cancel:{session_id}", settings.CHAT_CANCEL_TTL, "1")
        return {"stopped": True}

    @staticmethod
    async def stop_chat_session(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        await AppBffService.ensure_chat_session_access(user_id, session_id, db)
        return await AppBffService.stop_chat(session_id, redis_client)

    @staticmethod
    def resolve_client_ip(
        forwarded_for: str | None,
        real_ip: str | None,
        request_client_host: str | None,
    ) -> str:
        forwarded = forwarded_for or real_ip
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request_client_host or "unknown"

    @staticmethod
    async def build_chat_state(
        session_id: str,
        user_id: str | None,
        payload: dict[str, Any],
        request_client_ip: str,
        redis_client: redis.Redis,
        rate_limit_key_prefix: str = "app_chat",
    ) -> ChatState:
        await ensure_rate_limit(
            redis_client,
            key=f"rl:{rate_limit_key_prefix}:{request_client_ip}",
            limit=30,
            window_seconds=60,
        )
        context_overrides = payload.get("client_context_overrides")
        context_overrides = dict(context_overrides) if isinstance(context_overrides, dict) else None
        attachments = payload.get("attachments")
        if isinstance(attachments, list):
            clean_attachments = [item for item in attachments if isinstance(item, dict)]
            if clean_attachments:
                context_overrides = context_overrides or {}
                context_overrides["attachments"] = clean_attachments
        scene = payload.get("scene") or "chat"
        inferred_intent = None if scene == "travel_planner" else AppBffService._infer_chat_intent(payload.get("message"))
        if inferred_intent:
            context_overrides = context_overrides or {}
            context_overrides.setdefault("intent", inferred_intent)
            forced_skill_ids = AppBffService._forced_skill_ids_for_intent(inferred_intent)
            if forced_skill_ids:
                existing_forced = context_overrides.get("forced_skill_ids")
                merged_forced = []
                if isinstance(existing_forced, list):
                    merged_forced.extend(item for item in existing_forced if isinstance(item, str))
                merged_forced.extend(item for item in forced_skill_ids if item not in merged_forced)
                context_overrides["forced_skill_ids"] = merged_forced
        if payload.get("travel_action"):
            context_overrides = context_overrides or {}
            context_overrides["travel_action"] = payload.get("travel_action")
        if isinstance(payload.get("travel_payload"), dict):
            context_overrides = context_overrides or {}
            context_overrides["travel_payload"] = payload.get("travel_payload")
        if payload.get("agent_id"):
            context_overrides = context_overrides or {}
            context_overrides["agent_id"] = payload.get("agent_id")
        if payload.get("plan_type"):
            context_overrides = context_overrides or {}
            context_overrides["plan_type"] = payload.get("plan_type")
        if payload.get("action"):
            context_overrides = context_overrides or {}
            context_overrides["action"] = payload.get("action")
        if isinstance(payload.get("payload"), dict):
            context_overrides = context_overrides or {}
            context_overrides["payload"] = payload.get("payload")

        return ChatState(
            session_id=session_id,
            user_id=user_id,
            message=payload.get("message"),
            scene=scene,
            agent_id=payload.get("agent_id"),
            plan_type=payload.get("plan_type"),
            context_overrides=context_overrides,
            provider=AppBffService.resolve_chat_provider(payload.get("model")) or payload.get("provider"),
            client_ip=request_client_ip,
            resume_from_checkpoint=bool(payload.get("resume_from_checkpoint")),
            checkpoint_ref=payload.get("checkpoint_ref"),
            replay_from_checkpoint=bool(payload.get("replay_from_checkpoint")),
            resume_payload=payload.get("resume_payload"),
        )

    @staticmethod
    async def _latest_travel_final_json(db: AsyncSession, session_id: str) -> dict[str, Any] | None:
        return await AppBffService._latest_plan_final_json(db, session_id, plan_type="travel")

    @staticmethod
    async def _latest_plan_final_json(
        db: AsyncSession,
        session_id: str,
        *,
        plan_type: str | None = None,
    ) -> dict[str, Any] | None:
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(desc(ChatMessage.created_at))
            .limit(10)
        )
        for row in result.scalars().all():
            payload = row.tool_payload_json if isinstance(row.tool_payload_json, dict) else {}
            answer = payload.get("answer")
            if not isinstance(answer, dict) or not answer.get("state"):
                continue
            if plan_type and answer.get("plan_type") not in {None, plan_type}:
                continue
            return answer
        return None

    @staticmethod
    async def _prepare_multi_agent_payload(
        db: AsyncSession,
        session_id: str,
        user_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        resolved_plan_type = payload.get("plan_type") or ("travel" if payload.get("scene") == "travel_planner" else None)
        latest = await AppBffService._latest_plan_final_json(
            db,
            session_id,
            plan_type=str(resolved_plan_type) if resolved_plan_type else None,
        )
        prepared = await AgentRouter().prepare_turn(
            session_id=session_id,
            user_id=user_id,
            payload=payload,
            latest_final_json=latest,
        )
        return prepared.payload

    @staticmethod
    async def _prepare_supervisor_payload(
        db: AsyncSession,
        session_id: str,
        user_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        next_payload = dict(payload)
        context_overrides = next_payload.get("client_context_overrides")
        context_overrides = dict(context_overrides) if isinstance(context_overrides, dict) else {}
        latest_travel = await AppBffService._latest_travel_final_json(db, session_id)
        if latest_travel:
            context_overrides["latest_travel_final_json"] = latest_travel
        if user_id:
            from app.domain.preferences.markdown_profile import build_preference_context, ensure_user_preference_file

            profile = await ensure_user_preference_file(user_id)
            preference_context = build_preference_context(profile)
            context_overrides["user_preference_md"] = preference_context
            context_overrides["food_profile"] = preference_context.get("profile") or {}
            context_overrides["travel_food_preferences"] = preference_context.get("profile") or {}
            context_overrides["travel_food_preference_summary"] = preference_context.get("summary")
        if context_overrides:
            next_payload["client_context_overrides"] = context_overrides
        return next_payload

    @staticmethod
    async def _merge_current_session_travel_context(
        db: AsyncSession,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        scene = payload.get("scene") or "chat"
        if scene != "travel_planner":
            return payload
        latest = await AppBffService._latest_travel_final_json(db, session_id)
        if not latest:
            return payload
        current = payload.get("travel_payload")
        current = current if isinstance(current, dict) else {}
        base = {
            "previous_final_json": latest,
            "state": latest.get("state"),
            "trip_meta": latest.get("trip_meta"),
            "sources": latest.get("sources"),
            "places": latest.get("places"),
            "candidates": latest.get("candidates"),
            "failed_places": latest.get("failed_places"),
            "itinerary": latest.get("itinerary"),
            "map": latest.get("map"),
            "raw_text": latest.get("raw_text"),
        }
        merged = {key: value for key, value in base.items() if value not in (None, [], {})}
        merged.update(current)
        next_payload = dict(payload)
        next_payload["travel_payload"] = merged
        return next_payload

    @staticmethod
    def _infer_chat_intent(message: Any) -> str | None:
        text = str(message or "")
        if not text:
            return None
        if any(token in text for token in ("路线", "导航", "怎么走", "怎么去")):
            return "route"
        if any(
            token in text
            for token in (
                "吃点啥",
                "吃什么",
                "吃的",
                "吃啥",
                "今天吃",
                "晚饭",
                "午饭",
                "早餐",
                "夜宵",
                "外卖",
                "餐厅",
                "饭店",
                "美食",
                "好吃",
                "周边吃",
                "附近吃",
                "附近美食",
                "推荐吃",
                "出去吃",
                "外面吃",
                "去哪吃",
                "换一家",
                "下一家",
                "第二家",
                "第三家",
                "近一点",
                "不辣",
                "做饭",
                "在家做",
                "家里做",
                "菜谱",
                "食谱",
                "冰箱",
                "食材",
                "自己做",
            )
        ):
            return "food"
        return None

    @staticmethod
    def _forced_skill_ids_for_intent(intent: str) -> list[str]:
        if intent == "food":
            return ["food_decision", "restaurant_finder"]
        if intent == "route":
            return ["route_planner"]
        return []

    @staticmethod
    async def prepare_chat_stream_state(
        session_id: str,
        user_id: str | None,
        payload: dict[str, Any] | None,
        db: AsyncSession,
        redis_client: redis.Redis,
        forwarded_for: str | None,
        real_ip: str | None,
        request_client_host: str | None,
        trace_id: str | None,
        rate_limit_key_prefix: str = "app_chat",
    ) -> ChatState:
        await AppBffService.ensure_chat_session_access(user_id, session_id, db)
        client_ip = AppBffService.resolve_client_ip(forwarded_for, real_ip, request_client_host)
        payload = await AppBffService._merge_current_session_travel_context(
            db,
            session_id,
            dict(payload or {}),
        )
        if str(getattr(settings, "AGENT_RUNTIME_MODE", "generic") or "generic").strip().lower() == "supervisor":
            payload = await AppBffService._prepare_supervisor_payload(
                db,
                session_id,
                user_id,
                payload,
            )
        else:
            payload = await AppBffService._prepare_multi_agent_payload(
                db,
                session_id,
                user_id,
                payload,
            )
        state = await AppBffService.build_chat_state(
            session_id=session_id,
            user_id=user_id,
            payload=payload,
            request_client_ip=client_ip,
            redis_client=redis_client,
            rate_limit_key_prefix=rate_limit_key_prefix,
        )
        requested_model = (payload or {}).get("model")
        resolved = await resolve_model_config(db, user_id, requested_model)
        state.provider = resolved.provider_value if resolved.source == "env" else None
        state.resolved_model_config = resolved.model_dump()
        logger.info(
            "chat_model_resolved session_id=%s requested_model=%s source=%s provider=%s provider_value=%s model=%s config_id=%s",
            session_id,
            requested_model,
            resolved.source,
            resolved.provider,
            resolved.provider_value,
            resolved.model_planner,
            resolved.config_id,
        )
        state.trace_id = trace_id
        return state

from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.app.service import AppBffService


class ChatAppService:
    """Chat-facing BFF boundary.

    The methods delegate to the legacy AppBffService while the large service is
    being split. New chat code should depend on this class instead of adding
    more chat responsibilities to AppBffService.
    """

    create_chat_session = staticmethod(AppBffService.create_chat_session)
    list_chat_sessions = staticmethod(AppBffService.list_chat_sessions)
    list_chat_messages = staticmethod(AppBffService.list_chat_messages)
    rename_chat_session = staticmethod(AppBffService.rename_chat_session)
    delete_chat_session = staticmethod(AppBffService.delete_chat_session)
    stop_chat_session = staticmethod(AppBffService.stop_chat_session)
    ensure_chat_session_access = staticmethod(AppBffService.ensure_chat_session_access)
    create_chat_attachment = staticmethod(AppBffService.create_chat_attachment)
    list_chat_models_for_user = staticmethod(AppBffService.list_chat_models_for_user)

    @staticmethod
    async def prepare_chat_stream_state(
        *,
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
    ):
        return await AppBffService.prepare_chat_stream_state(
            session_id=session_id,
            user_id=user_id,
            payload=payload,
            db=db,
            redis_client=redis_client,
            forwarded_for=forwarded_for,
            real_ip=real_ip,
            request_client_host=request_client_host,
            trace_id=trace_id,
            rate_limit_key_prefix=rate_limit_key_prefix,
        )

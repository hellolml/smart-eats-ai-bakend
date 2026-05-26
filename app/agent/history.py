from __future__ import annotations

import json
from collections import OrderedDict
from contextvars import ContextVar
import time
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import settings
from app.infra.models.chat import ChatMessage, ChatSession

_DEFAULT_LOCAL_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_CURRENT_CACHE: ContextVar["_HistoryCache | None"] = ContextVar("chat_history_cache", default=None)


class _HistoryCache:
    def get(self, session_id: str) -> tuple[list[dict[str, Any]], str] | None:
        raise NotImplementedError

    def set(self, session_id: str, history: list[dict[str, Any]], version: str) -> None:
        raise NotImplementedError

    def delete(self, session_id: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return


class _LruHistoryCache(_HistoryCache):
    def __init__(self, cache: OrderedDict[str, dict[str, Any]]) -> None:
        self._cache = cache

    def get(self, session_id: str) -> tuple[list[dict[str, Any]], str] | None:
        entry = self._cache.get(session_id)
        if entry is None:
            return None
        if entry.get("expires_at") and entry["expires_at"] < time.time():
            self.delete(session_id)
            return None
        self._cache.move_to_end(session_id)
        history = entry.get("history") or []
        version = entry.get("version") or ""
        return [item.copy() for item in history], version

    def set(self, session_id: str, history: list[dict[str, Any]], version: str) -> None:
        expires_at = time.time() + settings.CHAT_HISTORY_LOCAL_CACHE_TTL_SECONDS
        self._cache[session_id] = {
            "history": [item.copy() for item in history],
            "version": version,
            "expires_at": expires_at,
        }
        self._cache.move_to_end(session_id)
        max_entries = settings.CHAT_HISTORY_LOCAL_CACHE_SIZE
        while len(self._cache) > max_entries:
            self._cache.popitem(last=False)

    def delete(self, session_id: str) -> None:
        self._cache.pop(session_id, None)


def create_history_cache() -> _HistoryCache:
    return _LruHistoryCache(OrderedDict())


def _default_local_cache() -> _HistoryCache:
    return _LruHistoryCache(_DEFAULT_LOCAL_CACHE)


def set_current_cache(cache: _HistoryCache | None) -> None:
    _CURRENT_CACHE.set(cache)


def get_current_cache() -> _HistoryCache | None:
    return _CURRENT_CACHE.get()


def clear_current_cache() -> None:
    _CURRENT_CACHE.set(None)


def _cache_mode() -> str:
    mode = (settings.CHAT_HISTORY_CACHE_MODE or "local_validate").lower()
    if mode not in {"local_first", "local_validate", "redis_only"}:
        return "local_validate"
    return mode


def _format_tool_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    if "result_preview" in payload:
        return json.dumps(payload.get("result_preview"), ensure_ascii=False)
    return ""


def _history_signature(history: list[dict[str, Any]]) -> str:
    if not history:
        return "0"
    last = history[-1]
    content = (last.get("content") or "")[:64]
    return f"{len(history)}:{last.get('role')}:{content}"


async def append_history_cache(
    redis_client: redis.Redis | None,
    session_id: str,
    item: dict[str, Any],
    local_cache: _HistoryCache | None = None,
) -> None:
    local_cache = local_cache or get_current_cache() or _default_local_cache()
    mode = _cache_mode()
    cached_local = local_cache.get(session_id)
    local_history = cached_local[0] if cached_local else []
    if not cached_local and redis_client:
        cache_key = f"chat:history:{session_id}"
        cached = await redis_client.get(cache_key)
        if cached:
            try:
                history = json.loads(cached)
            except json.JSONDecodeError:
                history = []
            if isinstance(history, list):
                local_history = history
    local_history.append(item)
    local_history = local_history[-settings.CHAT_HISTORY_CACHE_LIMIT:]
    version = _history_signature(local_history)
    if mode != "redis_only":
        local_cache.set(session_id, local_history, version)

    if not redis_client:
        return
    cache_key = f"chat:history:{session_id}"
    version_key = f"chat:history:{session_id}:sig"
    await redis_client.set(
        cache_key,
        json.dumps(local_history, ensure_ascii=True),
        ex=settings.CHAT_HISTORY_CACHE_TTL_SECONDS,
    )
    await redis_client.set(
        version_key,
        version,
        ex=settings.CHAT_HISTORY_CACHE_TTL_SECONDS,
    )


async def clear_session_cache(
    redis_client: redis.Redis | None,
    session_id: str,
    local_cache: _HistoryCache | None = None,
) -> None:
    local_cache = local_cache or get_current_cache()
    if local_cache:
        local_cache.delete(session_id)
    else:
        _default_local_cache().delete(session_id)
    if not redis_client:
        return
    await redis_client.delete(
        f"chat:history:{session_id}",
        f"chat:history:{session_id}:sig",
        f"chat:summary:{session_id}",
        f"chat:summary:{session_id}:pending",
        f"chat:summary:{session_id}:sig",
        f"chat:cancel:{session_id}",
        f"chat:pause:{session_id}",
    )


async def save_user_message(
    db: AsyncSession,
    redis_client: redis.Redis | None,
    session_id: str,
    content: str,
    update_title: bool = True,
    title_max_len: int = 24,
) -> None:
    last_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    last_result = await db.execute(last_stmt)
    last_msg = last_result.scalar_one_or_none()
    if last_msg and (last_msg.content or "") == content:
        return
    if update_title:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session and (not session.title or session.title == "新会话"):
            title = content.strip().replace("\n", " ")
            session.title = title[:title_max_len] if len(title) > title_max_len else title
    msg = ChatMessage(
        id=str(uuid4()),
        session_id=session_id,
        role="user",
        content=content,
    )
    db.add(msg)
    await db.commit()
    await append_history_cache(
        redis_client,
        session_id,
        {"role": "user", "content": content},
    )


async def save_tool_message(
    db: AsyncSession,
    redis_client: redis.Redis | None,
    session_id: str,
    tool_name: str,
    payload: dict[str, Any],
) -> None:
    last_stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "tool",
            ChatMessage.tool_name == tool_name,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    last_result = await db.execute(last_stmt)
    last_msg = last_result.scalar_one_or_none()
    if last_msg and last_msg.tool_payload_json == payload:
        return
    content = _format_tool_payload(payload)
    msg = ChatMessage(
        id=str(uuid4()),
        session_id=session_id,
        role="tool",
        tool_name=tool_name,
        content=content or None,
        tool_payload_json=payload,
    )
    db.add(msg)
    await db.commit()
    await append_history_cache(
        redis_client,
        session_id,
        {
            "role": "tool",
            "name": tool_name,
            "content": content or "",
        },
    )


async def save_assistant_message(
    db: AsyncSession,
    redis_client: redis.Redis | None,
    session_id: str,
    content: str,
    final_json: dict[str, Any] | None,
) -> None:
    last_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    last_result = await db.execute(last_stmt)
    last_msg = last_result.scalar_one_or_none()
    if last_msg and (last_msg.content or "") == content:
        return
    payload = {"answer": final_json}
    msg = ChatMessage(
        id=str(uuid4()),
        session_id=session_id,
        role="assistant",
        content=content,
        tool_payload_json=payload,
    )
    db.add(msg)
    await db.commit()
    await append_history_cache(
        redis_client,
        session_id,
        {"role": "assistant", "content": content},
    )

from __future__ import annotations

import asyncio
import json
import re
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
import logging
import os
from typing import Any, AsyncGenerator, Callable

import httpx
from openai import AsyncOpenAI

from app.agent.schemas import AgentAction, AgentActionModel, IntentDecision, ToolAction
from app.common.config import settings

logger = logging.getLogger("llm")
_LLM_LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("llm_log_context", default={})


def set_llm_log_context(values: dict[str, Any]) -> Token:
    return _LLM_LOG_CONTEXT.set(values)


def reset_llm_log_context(token: Token) -> None:
    _LLM_LOG_CONTEXT.reset(token)


def _log_context() -> dict[str, Any]:
    data = _LLM_LOG_CONTEXT.get() or {}
    return {
        "session_id": data.get("session_id"),
        "turn": data.get("turn"),
        "step": data.get("step"),
    }


def _request_log_mode() -> str:
    return (settings.LLM_REQUEST_LOG or "none").lower()


def _should_log_request(kind: str) -> bool:
    mode = _request_log_mode()
    if mode in {"none", "off", "disabled"}:
        return False
    if mode == "both":
        return True
    return mode == kind


def _extract_response_text(response: Any) -> str | None:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for item in output:
        if getattr(item, "type", None) == "output_text":
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        content = getattr(item, "content", None)
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts) if parts else None

@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str | None
    base_url: str
    model_planner: str
    model_writer: str


class ProviderRegistry:
    @staticmethod
    def get(provider: str | None) -> ProviderConfig:
        key = (provider or settings.LLM_PROVIDER or "qwen").lower()
        if key == "deepseek":
            return ProviderConfig(
                name="deepseek",
                api_key=os.getenv("DEEPSEEK_API_KEY") or settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                model_planner=settings.DEEPSEEK_MODEL_PLANNER,
                model_writer=settings.DEEPSEEK_MODEL_WRITER,
            )
        if key == "qwen":
            return ProviderConfig(
                name="qwen",
                api_key=os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY,
                base_url=settings.QWEN_BASE_URL,
                model_planner=settings.QWEN_MODEL_PLANNER,
                model_writer=settings.QWEN_MODEL_WRITER,
            )
        return ProviderConfig(
            name="openai",
            api_key=os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model_planner=settings.OPENAI_MODEL_PLANNER,
            model_writer=settings.OPENAI_MODEL_WRITER,
        )


_CLIENT_POOL: dict[str, AsyncOpenAI] = {}
_CLIENT_POOL_LOCK = threading.Lock()


def _get_shared_client(config: ProviderConfig) -> AsyncOpenAI | None:
    """获取或创建共享的 AsyncOpenAI 客户端，复用 TCP/TLS 连接。"""
    if not config.api_key:
        return None
    key = f"{config.name}:{config.base_url}"
    client = _CLIENT_POOL.get(key)
    if client is not None:
        return client
    with _CLIENT_POOL_LOCK:
        client = _CLIENT_POOL.get(key)
        if client is not None:
            return client
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=1,
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
        _CLIENT_POOL[key] = client
        return client


class OpenAIPlanner:
    def __init__(self, provider: str | None = None) -> None:
        self.config = ProviderRegistry.get(provider)
        self.client = _get_shared_client(self.config)

    async def plan(
        self,
        system: str,
        user: str,
        action_normalizer: Callable[[str], AgentAction | None] | None = None,
    ) -> AgentAction:
        if not self.client:
            raise RuntimeError("LLM provider is not configured")
        context = _log_context()
        if _should_log_request("system"):
            logger.info(
                "planner request provider=%s model=%s session_id=%s turn=%s step=%s system=%s",
                self.config.name,
                self.config.model_planner,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                system,
            )
        if _should_log_request("user"):
            logger.info(
                "planner request provider=%s model=%s session_id=%s turn=%s step=%s user=%s",
                self.config.name,
                self.config.model_planner,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                user,
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        # 统一使用 chat.completions 流式接口，收集完整内容后解析。
        # 流式调用可以更早建立连接、降低 TTFB，部分提供商流式首 token 更快。
        content = await self._stream_collect(messages)

        logger.info(
            "planner response provider=%s model=%s session_id=%s turn=%s step=%s raw=%s",
            self.config.name,
            self.config.model_planner,
            context.get("session_id"),
            context.get("turn"),
            context.get("step"),
            content,
        )
        if action_normalizer:
            mapped = action_normalizer(content)
            if mapped:
                return mapped
        try:
            return AgentActionModel.model_validate_json(content).root
        except Exception as exc:
            raise RuntimeError(f"invalid planner response: {exc}") from exc

    async def _stream_collect(self, messages: list[dict[str, str]]) -> str:
        """流式收集 planner 响应，减少 TTFB。"""
        stream = await self.client.chat.completions.create(
            model=self.config.model_planner,
            messages=messages,
            stream=True,
        )
        chunks: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
        content = "".join(chunks)
        if not content:
            raise RuntimeError("invalid planner response: empty content")
        return content

    async def classify_intent(self, user: str, context: dict[str, Any] | None = None) -> IntentDecision:
        """让 LLM 输出结构化意图，代码仅做 schema 校验与兜底。"""
        if not self.client:
            return IntentDecision()

        system = (
            "You are an intent classifier for a food assistant. "
            "Return strict JSON only with fields: "
            "intent, confidence, slots, need_clarify, clarify_question. "
            "intent must be one of: eat_out, cook_home, route, chat, unknown."
        )
        payload = {
            "message": user,
            "context": context or {},
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            raw = await self._stream_collect(messages)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r"\s*```$", "", cleaned).strip()
            data = json.loads(cleaned)
            return IntentDecision.model_validate(data)
        except Exception as exc:
            logger.info("intent_classify_fallback reason=%s", str(exc))
            return IntentDecision()


class OpenAIWriter:
    def __init__(self, provider: str | None = None) -> None:
        self.config = ProviderRegistry.get(provider)
        self.client = _get_shared_client(self.config)

    async def stream(self, system: str, user: str) -> AsyncGenerator[str, None]:
        if not self.client:
            raise RuntimeError("LLM provider is not configured")
        context = _log_context()
        if _should_log_request("system"):
            logger.info(
                "writer request provider=%s model=%s session_id=%s turn=%s step=%s system=%s",
                self.config.name,
                self.config.model_writer,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                system,
            )
        if _should_log_request("user"):
            logger.info(
                "writer request provider=%s model=%s session_id=%s turn=%s step=%s user=%s",
                self.config.name,
                self.config.model_writer,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                user,
            )

        # 非 OpenAI 兼容提供商（如 qwen/deepseek）在 responses 流式上可能只返回最终块，
        # 这里优先仅对 openai 使用 responses API，其它提供商走 chat.completions 的流式分片。
        if hasattr(self.client, "responses") and self.config.name == "openai":
            stream = await self.client.responses.create(
                model=self.config.model_writer,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                stream=True,
            )
            chunks: list[str] = []
            async for event in stream:
                if event.type == "response.output_text.delta":
                    chunks.append(event.delta)
                    yield event.delta
                elif event.type == "response.completed":
                    break
                elif event.type == "response.failed":
                    raise RuntimeError("writer response failed")
            logger.info(
                "writer response provider=%s model=%s session_id=%s turn=%s step=%s text=%s",
                self.config.name,
                self.config.model_writer,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                "".join(chunks),
            )
            return

        stream = await self.client.chat.completions.create(
            model=self.config.model_writer,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=True,
        )
        chunks: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
                yield delta
        logger.info(
            "writer response provider=%s model=%s session_id=%s turn=%s step=%s text=%s",
            self.config.name,
            self.config.model_writer,
            context.get("session_id"),
            context.get("turn"),
            context.get("step"),
            "".join(chunks),
        )

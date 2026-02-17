from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass
import logging
import os
from typing import Any, AsyncGenerator, Callable

from openai import AsyncOpenAI

from app.agent.schemas import AgentAction, AgentActionModel, ToolAction
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


class OpenAIPlanner:
    def __init__(self, provider: str | None = None) -> None:
        self.config = ProviderRegistry.get(provider)
        if not self.config.api_key:
            self.client = None
        else:
            self.client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
            )

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

        if hasattr(self.client, "responses"):
            response = await self.client.responses.create(
                model=self.config.model_planner,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = _extract_response_text(response)
            if not content:
                raise RuntimeError("invalid planner response: empty content")
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

        response = await self.client.chat.completions.create(
            model=self.config.model_planner,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content or ""
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


class OpenAIWriter:
    def __init__(self, provider: str | None = None) -> None:
        self.config = ProviderRegistry.get(provider)
        if not self.config.api_key:
            self.client = None
        else:
            self.client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
            )

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

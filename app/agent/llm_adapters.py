from __future__ import annotations

import json
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
import logging
import os
from typing import Any, AsyncGenerator, Callable

import httpx
from openai import AsyncOpenAI

from app.agent.schemas import (
    AgentAction,
    FinalAction,
    FinalAnswer,
    FinalAnswerArgs,
    IntentDecision,
    IntentDecisionArgs,
    ToolCallsAction,
)
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
        raw = (provider or settings.LLM_PROVIDER or "qwen").strip().lower()
        provider_key, _, model_override = raw.partition(":")
        key = provider_key or "qwen"
        override = model_override.strip() or None
        if key == "deepseek":
            return ProviderConfig(
                name="deepseek",
                api_key=os.getenv("DEEPSEEK_API_KEY") or settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                model_planner=override or settings.DEEPSEEK_MODEL_PLANNER,
                model_writer=override or settings.DEEPSEEK_MODEL_WRITER,
            )
        if key == "qwen":
            return ProviderConfig(
                name="qwen",
                api_key=os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY,
                base_url=settings.QWEN_BASE_URL,
                model_planner=override or settings.QWEN_MODEL_PLANNER,
                model_writer=override or settings.QWEN_MODEL_WRITER,
            )
        return ProviderConfig(
            name="openai",
            api_key=os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model_planner=override or settings.OPENAI_MODEL_PLANNER,
            model_writer=override or settings.OPENAI_MODEL_WRITER,
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
        available_tools: list[dict[str, Any]],
        action_normalizer: Callable[[str], AgentAction | None] | None = None,
    ) -> AgentAction:
        decision = await self.plan_tool_calls(system, user, available_tools)
        content = decision.get("content") if isinstance(decision, dict) else ""
        normalized_calls = decision.get("tool_calls") if isinstance(decision, dict) else []

        if isinstance(normalized_calls, list) and normalized_calls:
            calls: list[dict[str, dict[str, Any]]] = []
            for call in normalized_calls:
                tool_name = call.get("name") if isinstance(call, dict) else None
                args = call.get("args") if isinstance(call, dict) else None
                if not isinstance(tool_name, str) or not isinstance(args, dict):
                    continue

                if tool_name == "submit_final_answer":
                    answer_args = FinalAnswerArgs.model_validate(args)
                    answer = FinalAnswer.model_validate(answer_args.model_dump())
                    return FinalAction(answer=answer)

                calls.append({tool_name: args})

            if calls:
                return ToolCallsAction(calls=calls)

        if isinstance(content, str) and content and action_normalizer:
            mapped = action_normalizer(content)
            if mapped:
                return mapped

        return self._build_fallback_final_action(content if isinstance(content, str) else "")

    async def plan_tool_calls(
        self,
        system: str,
        user: str,
        available_tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
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
        openai_tools = self._build_openai_tools(available_tools)

        response = await self.client.chat.completions.create(
            model=self.config.model_planner,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
            timeout=35,
        )

        message = response.choices[0].message
        content = self._message_content_to_text(getattr(message, "content", None))
        raw_tool_calls = getattr(message, "tool_calls", None) or []

        logger.info(
            "planner response provider=%s model=%s session_id=%s turn=%s step=%s tool_calls=%s content=%s",
            self.config.name,
            self.config.model_planner,
            context.get("session_id"),
            context.get("turn"),
            context.get("step"),
            [getattr(getattr(item, "function", None), "name", None) for item in raw_tool_calls],
            content,
        )

        normalized_calls: list[dict[str, Any]] = []
        for index, call in enumerate(raw_tool_calls):
            function = getattr(call, "function", None)
            tool_name = getattr(function, "name", None)
            if not isinstance(tool_name, str) or not tool_name:
                raise RuntimeError("invalid planner response: missing tool name")
            args = self._normalize_tool_args(tool_name, getattr(function, "arguments", None))
            call_id = getattr(call, "id", None)
            if not isinstance(call_id, str) or not call_id:
                call_id = f"call_{index}"
            normalized_calls.append(
                {
                    "name": tool_name,
                    "args": args,
                    "id": call_id,
                    "type": "tool_call",
                }
            )

        return {
            "content": content,
            "tool_calls": normalized_calls,
        }

    def final_action_from_text(self, content: str) -> FinalAction:
        return self._build_fallback_final_action(content)

    def _build_openai_tools(self, available_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for item in available_tools:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            description = item.get("description")
            parameters = item.get("parameters") or item.get("input_schema")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}}
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(description or ""),
                        "parameters": parameters,
                    },
                }
            )

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "submit_final_answer",
                    "description": "当你已收集足够信息并准备给用户最终回复时调用。",
                    "parameters": FinalAnswerArgs.model_json_schema(),
                },
            }
        )
        return tools

    def _normalize_tool_args(self, tool_name: str, raw_args: Any) -> dict[str, Any]:
        payload = raw_args if isinstance(raw_args, str) else json.dumps(raw_args or {}, ensure_ascii=False)
        try:
            data = json.loads(payload) if payload else {}
        except Exception as exc:
            raise RuntimeError(f"invalid tool arguments for {tool_name}: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"invalid tool arguments for {tool_name}: expected object")
        return data

    def _message_content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        return ""

    def _build_fallback_final_action(self, content: str) -> FinalAction:
        text = content.strip() if isinstance(content, str) else ""
        if not text:
            text = "好的。"
        return FinalAction(
            answer=FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": text,
                        "reason": "direct_text_response",
                    }
                ],
                followups=[],
                warnings=[],
            )
        )

    async def classify_intent(self, user: str, context: dict[str, Any] | None = None) -> IntentDecision:
        """让 LLM 通过原生 function calling 输出结构化意图。"""
        if not self.client:
            return IntentDecision()

        system = (
            "You are an intent classifier for a food assistant. "
            "Choose exactly one intent and call decide_intent with structured arguments. "
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

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "decide_intent",
                    "description": "输出用户意图分类结果。",
                    "parameters": IntentDecisionArgs.model_json_schema(),
                },
            }
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.config.model_planner,
                messages=messages,
                tools=tools,
                tool_choice={"type": "function", "function": {"name": "decide_intent"}},
                timeout=20,
            )
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                return IntentDecision()

            function = getattr(tool_calls[0], "function", None)
            if getattr(function, "name", None) != "decide_intent":
                return IntentDecision()
            args = self._normalize_tool_args("decide_intent", getattr(function, "arguments", None))
            strict = IntentDecisionArgs.model_validate(args)
            return IntentDecision.model_validate(strict.model_dump())
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

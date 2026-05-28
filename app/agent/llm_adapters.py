from __future__ import annotations

import hashlib
import json
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
import logging
import os
from typing import Any, AsyncGenerator, Callable

import httpx
from openai import AsyncOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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
    model_vision_planner: str | None = None
    client_pool_key: str | None = None


class ProviderRegistry:
    @staticmethod
    def from_resolved_config(config: dict[str, Any]) -> ProviderConfig:
        api_key = config.get("api_key")
        fingerprint = ""
        if isinstance(api_key, str) and api_key:
            fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        config_id = str(config.get("config_id") or "custom")
        model_planner = str(config.get("model_planner") or "")
        model_writer = str(config.get("model_writer") or model_planner)
        return ProviderConfig(
            name=str(config.get("provider") or config.get("display_name") or "openai_compatible"),
            api_key=api_key if isinstance(api_key, str) else None,
            base_url=str(config.get("base_url") or ""),
            model_planner=model_planner,
            model_writer=model_writer,
            model_vision_planner=config.get("model_vision_planner") if isinstance(config.get("model_vision_planner"), str) else None,
            client_pool_key=f"config:{config_id}:{fingerprint}",
        )

    @staticmethod
    def get(provider: str | None) -> ProviderConfig:
        raw = (provider or settings.LLM_PROVIDER or "qwen").strip().lower()
        provider_key, _, model_override = raw.partition(":")
        key = provider_key or "qwen"
        override = model_override.strip() or None
        vision_model = settings.LLM_VISION_MODEL_PLANNER or None
        if key == "deepseek":
            return ProviderConfig(
                name="deepseek",
                api_key=os.getenv("DEEPSEEK_API_KEY") or settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                model_planner=override or settings.DEEPSEEK_MODEL_PLANNER,
                model_writer=override or settings.DEEPSEEK_MODEL_WRITER,
                model_vision_planner=vision_model,
            )
        if key == "qwen":
            return ProviderConfig(
                name="qwen",
                api_key=os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY,
                base_url=settings.QWEN_BASE_URL,
                model_planner=override or settings.QWEN_MODEL_PLANNER,
                model_writer=override or settings.QWEN_MODEL_WRITER,
                model_vision_planner=vision_model,
            )
        return ProviderConfig(
            name="openai",
            api_key=os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model_planner=override or settings.OPENAI_MODEL_PLANNER,
            model_writer=override or settings.OPENAI_MODEL_WRITER,
            model_vision_planner=vision_model,
        )


_CLIENT_POOL: dict[str, AsyncOpenAI] = {}
_CLIENT_POOL_LOCK = threading.Lock()
_ANTHROPIC_CLIENT_POOL: dict[str, httpx.AsyncClient] = {}
_ANTHROPIC_CLIENT_POOL_LOCK = threading.Lock()


def _get_shared_client(config: ProviderConfig) -> AsyncOpenAI | None:
    """获取或创建共享的 AsyncOpenAI 客户端，复用 TCP/TLS 连接。"""
    if not config.api_key:
        return None
    key = config.client_pool_key or f"{config.name}:{config.base_url}"
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


def _get_shared_anthropic_client(config: ProviderConfig) -> httpx.AsyncClient | None:
    if not config.api_key:
        return None
    key = config.client_pool_key or f"{config.name}:{config.base_url}"
    client = _ANTHROPIC_CLIENT_POOL.get(key)
    if client is not None:
        return client
    with _ANTHROPIC_CLIENT_POOL_LOCK:
        client = _ANTHROPIC_CLIENT_POOL.get(key)
        if client is not None:
            return client
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        _ANTHROPIC_CLIENT_POOL[key] = client
        return client


def _anthropic_messages_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    return f"{root}/messages" if root.endswith("/v1") else f"{root}/v1/messages"


class OpenAIPlanner:
    def __init__(self, provider: str | None = None, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderRegistry.get(provider)
        self.client = _get_shared_client(self.config)

    async def ainvoke_with_tools(
        self,
        messages: list[Any],
        tools: list[Any],
        image_parts: list[dict[str, Any]] | None = None,
    ) -> AIMessage:
        available_tools = self._langchain_tools_to_available_schemas(tools)
        plan_tool_calls_overridden = getattr(type(self).plan_tool_calls, "__module__", __name__) != __name__
        if self._is_simple_system_user_turn(messages) or plan_tool_calls_overridden:
            system, user = (
                self._messages_to_system_latest_user(messages)
                if plan_tool_calls_overridden and not self._is_simple_system_user_turn(messages)
                else self._messages_to_system_user(messages)
            )
            if image_parts:
                decision = await self.plan_tool_calls(
                    system,
                    user,
                    available_tools,
                    image_parts=image_parts,
                )
            else:
                decision = await self.plan_tool_calls(system, user, available_tools)
        else:
            decision = await self.plan_native_messages_with_tools(
                messages,
                available_tools,
                image_parts=image_parts,
            )
        content = decision.get("content") if isinstance(decision, dict) else ""
        tool_calls = decision.get("tool_calls") if isinstance(decision, dict) else []
        return AIMessage(
            content=content if isinstance(content, str) else "",
            tool_calls=tool_calls if isinstance(tool_calls, list) else [],
        )

    def _messages_to_system_user(self, messages: list[Any]) -> tuple[str, str]:
        system_parts: list[str] = []
        user_parts: list[str] = []
        for message in messages:
            content = self._message_content_to_text(getattr(message, "content", None))
            if not content:
                continue
            if isinstance(message, SystemMessage):
                system_parts.append(content)
            elif isinstance(message, HumanMessage):
                user_parts.append(f"user: {content}" if user_parts else content)
            elif isinstance(message, AIMessage):
                user_parts.append(f"assistant: {content}")
            else:
                message_type = getattr(message, "type", None) or getattr(message, "role", None) or "context"
                user_parts.append(f"{message_type}: {content}")
        return "\n".join(system_parts).strip(), "\n".join(user_parts).strip()

    def _messages_to_system_latest_user(self, messages: list[Any]) -> tuple[str, str]:
        system_parts: list[str] = []
        latest_user = ""
        for message in messages:
            content = self._message_content_to_text(getattr(message, "content", None))
            if isinstance(message, SystemMessage) and content:
                system_parts.append(content)
            elif isinstance(message, HumanMessage) and content:
                latest_user = content
        return "\n".join(system_parts).strip(), latest_user

    @staticmethod
    def _is_simple_system_user_turn(messages: list[Any]) -> bool:
        return (
            len(messages) == 2
            and isinstance(messages[0], SystemMessage)
            and isinstance(messages[1], HumanMessage)
        )

    def _langchain_tools_to_available_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        available: list[dict[str, Any]] = []
        for tool in tools:
            name = getattr(tool, "name", None)
            if not isinstance(name, str) or not name or name == "submit_final_answer":
                continue
            tool_call_schema = getattr(tool, "tool_call_schema", None)
            if hasattr(tool_call_schema, "model_json_schema"):
                input_schema = tool_call_schema.model_json_schema()
            else:
                args = getattr(tool, "args", None)
                input_schema = {"type": "object", "properties": args} if isinstance(args, dict) else {"type": "object", "properties": {}}
            available.append(
                {
                    "name": name,
                    "description": str(getattr(tool, "description", "") or ""),
                    "input_schema": input_schema,
                }
            )
        return available

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
        image_parts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("LLM provider is not configured")
        context = _log_context()
        requested_image_parts = image_parts if isinstance(image_parts, list) else []
        model = (
            self.config.model_vision_planner
            if requested_image_parts and self.config.model_vision_planner
            else self.config.model_planner
        )
        if _should_log_request("system"):
            logger.info(
                "planner request provider=%s model=%s session_id=%s turn=%s step=%s system=%s",
                self.config.name,
                model,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                system,
            )
        if _should_log_request("user"):
            logger.info(
                "planner request provider=%s model=%s session_id=%s turn=%s step=%s user=%s",
                self.config.name,
                model,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                user,
            )

        user_content: str | list[dict[str, Any]]
        if requested_image_parts:
            user_content = [{"type": "text", "text": user}, *requested_image_parts]
        else:
            user_content = user
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        openai_tools = self._build_openai_tools(available_tools)

        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
            timeout=settings.LLM_PLANNER_REQUEST_TIMEOUT_SECONDS,
        )

        message = response.choices[0].message
        content = self._message_content_to_text(getattr(message, "content", None))
        raw_tool_calls = getattr(message, "tool_calls", None) or []

        logger.info(
            "planner response provider=%s model=%s session_id=%s turn=%s step=%s tool_calls=%s content=%s",
            self.config.name,
            model,
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

    async def plan_native_messages_with_tools(
        self,
        messages: list[Any],
        available_tools: list[dict[str, Any]],
        image_parts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("LLM provider is not configured")
        context = _log_context()
        requested_image_parts = image_parts if isinstance(image_parts, list) else []
        model = (
            self.config.model_vision_planner
            if requested_image_parts and self.config.model_vision_planner
            else self.config.model_planner
        )
        payload_messages = self._messages_to_openai_payload(
            messages,
            requested_image_parts,
        )
        if _should_log_request("user"):
            logger.info(
                "planner request provider=%s model=%s session_id=%s turn=%s step=%s messages=%s",
                self.config.name,
                model,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                payload_messages,
            )
        response = await self.client.chat.completions.create(
            model=model,
            messages=payload_messages,
            tools=self._build_openai_tools(available_tools),
            tool_choice="auto",
            timeout=settings.LLM_PLANNER_REQUEST_TIMEOUT_SECONDS,
        )

        message = response.choices[0].message
        content = self._message_content_to_text(getattr(message, "content", None))
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        logger.info(
            "planner response provider=%s model=%s session_id=%s turn=%s step=%s tool_calls=%s content=%s",
            self.config.name,
            model,
            context.get("session_id"),
            context.get("turn"),
            context.get("step"),
            [getattr(getattr(item, "function", None), "name", None) for item in raw_tool_calls],
            content,
        )
        normalized_calls = [
            self._normalize_openai_tool_call(item, index)
            for index, item in enumerate(raw_tool_calls)
            if getattr(item, "function", None) is not None
        ]
        return {"content": content, "tool_calls": normalized_calls}

    def _messages_to_openai_payload(
        self,
        messages: list[Any],
        image_parts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for message in messages:
            content = self._message_content_to_text(getattr(message, "content", None))
            if not content:
                continue
            if isinstance(message, SystemMessage):
                payload.append({"role": "system", "content": content})
            elif isinstance(message, HumanMessage):
                payload.append({"role": "user", "content": content})
            elif isinstance(message, AIMessage):
                payload.append({"role": "assistant", "content": content})
            else:
                message_type = getattr(message, "type", None) or getattr(message, "role", None) or "context"
                payload.append({"role": "user", "content": f"{message_type}: {content}"})
        if not payload:
            payload.append({"role": "user", "content": ""})
        if image_parts:
            for item in reversed(payload):
                if item.get("role") == "user":
                    text = item.get("content") if isinstance(item.get("content"), str) else ""
                    item["content"] = [{"type": "text", "text": text}, *image_parts]
                    break
        return payload

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

    def _normalize_openai_tool_call(self, call: Any, index: int) -> dict[str, Any]:
        function = getattr(call, "function", None)
        tool_name = getattr(function, "name", None)
        if not isinstance(tool_name, str) or not tool_name:
            raise RuntimeError("invalid planner response: missing tool name")
        args = self._normalize_tool_args(tool_name, getattr(function, "arguments", None))
        call_id = getattr(call, "id", None)
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{index}"
        return {
            "name": tool_name,
            "args": args,
            "id": call_id,
            "type": "tool_call",
        }

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
                timeout=settings.LLM_INTENT_REQUEST_TIMEOUT_SECONDS,
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


class AnthropicPlanner(OpenAIPlanner):
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.client = _get_shared_anthropic_client(config)

    async def ainvoke_with_tools(
        self,
        messages: list[Any],
        tools: list[Any],
        image_parts: list[dict[str, Any]] | None = None,
    ) -> AIMessage:
        system, user = self._messages_to_system_user(messages)
        available_tools = self._langchain_tools_to_available_schemas(tools)
        decision = await self.plan_tool_calls(
            system,
            user,
            available_tools,
            image_parts=image_parts,
        )
        content = decision.get("content") if isinstance(decision, dict) else ""
        tool_calls = decision.get("tool_calls") if isinstance(decision, dict) else []
        return AIMessage(
            content=content if isinstance(content, str) else "",
            tool_calls=tool_calls if isinstance(tool_calls, list) else [],
        )

    async def plan_tool_calls(
        self,
        system: str,
        user: str,
        available_tools: list[dict[str, Any]],
        image_parts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("LLM provider is not configured")
        context = _log_context()
        requested_image_parts = image_parts if isinstance(image_parts, list) else []
        model = (
            self.config.model_vision_planner
            if requested_image_parts and self.config.model_vision_planner
            else self.config.model_planner
        )
        if _should_log_request("system"):
            logger.info(
                "planner request provider=%s model=%s session_id=%s turn=%s step=%s system=%s",
                self.config.name,
                model,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                system,
            )
        if _should_log_request("user"):
            logger.info(
                "planner request provider=%s model=%s session_id=%s turn=%s step=%s user=%s",
                self.config.name,
                model,
                context.get("session_id"),
                context.get("turn"),
                context.get("step"),
                user,
            )

        response = await self.client.post(
            _anthropic_messages_url(self.config.base_url),
            headers={
                "x-api-key": self.config.api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 2048,
                "system": system,
                "messages": [
                    {
                        "role": "user",
                        "content": self._build_anthropic_user_content(user, requested_image_parts),
                    }
                ],
                "tools": self._build_anthropic_tools(available_tools),
                "tool_choice": {"type": "auto"},
            },
        )
        response.raise_for_status()
        data = response.json()
        content_blocks = data.get("content") if isinstance(data, dict) else []
        if not isinstance(content_blocks, list):
            content_blocks = []

        text_parts: list[str] = []
        normalized_calls: list[dict[str, Any]] = []
        for index, block in enumerate(content_blocks):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"].strip())
                continue
            if block_type != "tool_use":
                continue
            tool_name = block.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                raise RuntimeError("invalid planner response: missing tool name")
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            call_id = block.get("id")
            normalized_calls.append(
                {
                    "name": tool_name,
                    "args": args,
                    "id": call_id if isinstance(call_id, str) and call_id else f"toolu_{index}",
                    "type": "tool_call",
                }
            )

        content = "\n".join(part for part in text_parts if part).strip()
        logger.info(
            "planner response provider=%s model=%s session_id=%s turn=%s step=%s tool_calls=%s content=%s",
            self.config.name,
            model,
            context.get("session_id"),
            context.get("turn"),
            context.get("step"),
            [item.get("name") for item in normalized_calls],
            content,
        )
        return {"content": content, "tool_calls": normalized_calls}

    def _build_anthropic_tools(self, available_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for item in available_tools:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            input_schema = item.get("parameters") or item.get("input_schema")
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            tools.append(
                {
                    "name": name,
                    "description": str(item.get("description") or ""),
                    "input_schema": input_schema,
                }
            )
        tools.append(
            {
                "name": "submit_final_answer",
                "description": "当你已收集足够信息并准备给用户最终回复时调用。",
                "input_schema": FinalAnswerArgs.model_json_schema(),
            }
        )
        return tools

    def _build_anthropic_user_content(self, user: str, image_parts: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
        if not image_parts:
            return user
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for item in image_parts:
            if not isinstance(item, dict):
                continue
            image_url = item.get("image_url")
            if item.get("type") != "image_url" or not isinstance(image_url, dict):
                continue
            url = image_url.get("url")
            if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
                continue
            media_prefix, data = url.split(",", 1)
            media_type = media_prefix.removeprefix("data:").removesuffix(";base64")
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            )
        return content

    async def classify_intent(self, user: str, context: dict[str, Any] | None = None) -> IntentDecision:
        return IntentDecision()


def build_planner(provider: str | None = None, config: ProviderConfig | None = None) -> OpenAIPlanner:
    if config and config.name == "anthropic":
        return AnthropicPlanner(config=config)
    return OpenAIPlanner(provider=provider, config=config)


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

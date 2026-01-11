from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from typing import Any, AsyncGenerator, Callable

from openai import AsyncOpenAI

from app.agent.schemas import AgentAction, AgentActionModel, ToolAction
from app.common.config import settings

logger = logging.getLogger("llm")

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

        if hasattr(self.client, "responses"):
            response = await self.client.responses.parse(
                model=self.config.model_planner,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text_format=AgentActionModel,
            )
            if settings.DEBUG:
                logger.info("planner response parsed=%s", response.output_parsed)
            return response.output_parsed.root

        response = await self.client.chat.completions.create(
            model=self.config.model_planner,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        if settings.DEBUG:
            logger.info("planner response raw=%s", content)
        if action_normalizer:
            mapped = action_normalizer(content)
            if mapped:
                return mapped
        return AgentActionModel.model_validate_json(content).root


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

        if hasattr(self.client, "responses"):
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
            if settings.DEBUG:
                logger.info("writer response text=%s", "".join(chunks))
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
        if settings.DEBUG:
            logger.info("writer response text=%s", "".join(chunks))

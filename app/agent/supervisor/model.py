from __future__ import annotations

import asyncio
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field, PrivateAttr

from app.agent.llm_adapters import ProviderRegistry, build_planner


class PlannerChatModel(BaseChatModel):
    provider: str | None = None
    resolved_model_config: dict[str, Any] | None = None
    bound_tools: list[Any] = Field(default_factory=list)
    _planner: Any = PrivateAttr(default=None)

    def __init__(self, *, planner: Any = None, **data: Any) -> None:
        super().__init__(**data)
        self._planner = planner

    @property
    def _llm_type(self) -> str:
        return "smart_eats_planner_chat_model"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"provider": self.provider}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool | Any],
        *,
        tool_choice: str | None = None,
        parallel_tool_calls: bool | None = None,
        **kwargs: Any,
    ) -> "PlannerChatModel":
        return PlannerChatModel(
            provider=self.provider,
            resolved_model_config=self.resolved_model_config,
            bound_tools=list(tools),
            planner=self._planner,
        )

    def _planner_instance(self) -> Any:
        if self._planner is not None:
            return self._planner
        config = (
            ProviderRegistry.from_resolved_config(self.resolved_model_config)
            if isinstance(self.resolved_model_config, dict)
            and self.resolved_model_config.get("source") == "user_config"
            else None
        )
        return build_planner(provider=self.provider, config=config)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        planner = self._planner_instance()
        message = await planner.ainvoke_with_tools(messages, list(self.bound_tools))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._agenerate(messages, stop=stop, run_manager=None, **kwargs))
        raise NotImplementedError("PlannerChatModel does not support synchronous generation inside an event loop")

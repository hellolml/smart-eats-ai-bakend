from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.context_engine.budget import BudgetManager
from app.context_engine.condenser import Condenser
from app.context_engine.memory import InMemoryVectorMemoryStore
from app.context_engine.stores import InMemoryConversationStore
from app.context_engine.tokenizer import ApproxTokenCounter
from app.context_engine.types import (
    BudgetReport,
    ContextBlock,
    ContextEvent,
    ContextRequest,
    MemoryRecord,
    PreparedContext,
)
from app.context_engine.view import ViewBuilder


class ContextEngine:
    def __init__(
        self,
        *,
        conversation_store=None,
        memory_store=None,
        token_counter: ApproxTokenCounter | None = None,
        providers: list[Any] | None = None,
        max_tokens: int = 8192,
        condenser: Condenser | None = None,
    ) -> None:
        self.conversation_store = conversation_store or InMemoryConversationStore()
        self.memory_store = memory_store or InMemoryVectorMemoryStore()
        self.token_counter = token_counter or ApproxTokenCounter()
        self.providers = providers or []
        self.condenser = condenser or Condenser(
            store=self.conversation_store,
            token_counter=self.token_counter,
        )
        self.budget_manager = BudgetManager(
            token_counter=self.token_counter,
            max_tokens=max_tokens,
            condenser=self.condenser,
        )

    async def prepare(self, request: ContextRequest) -> PreparedContext:
        await self.conversation_store.ensure_thread(
            request.thread_id,
            user_id=request.user_id,
            scene=request.scene,
        )
        blocks = await self._collect_blocks(request)
        memories = await self._retrieve_memories(request)
        memory_blocks = [
            ContextBlock(
                kind="memory",
                source="memory_store",
                content=self._render_memory(item),
                priority=60,
                metadata={"memory_id": item.id, "score": item.score},
            )
            for item in memories
        ]
        all_blocks = [*blocks, *memory_blocks]
        memory_namespace = ("user", request.user_id) if request.user_id else None
        budget_report = await self.budget_manager.fit_thread(
            request.thread_id,
            all_blocks,
            memory_namespace=memory_namespace,
        )
        view = await ViewBuilder(self.conversation_store).build(request.thread_id)
        messages = self._compose_messages(
            request=request,
            events=view.events,
            blocks=all_blocks,
            budget_report=budget_report,
        )
        message_tokens = self.token_counter.count_messages(messages)
        if message_tokens > budget_report.total_tokens:
            budget_report.total_tokens = min(message_tokens, budget_report.max_tokens)
            budget_report.buckets["system"] = max(
                budget_report.buckets.get("system", 0),
                self.token_counter.count_text(request.system_prompt),
            )
            if request.message:
                budget_report.buckets["current_user"] = max(
                    budget_report.buckets.get("current_user", 0),
                    self.token_counter.count_text(request.message),
                )
        return PreparedContext(
            messages=messages,
            blocks=all_blocks,
            memories=memories,
            budget_report=budget_report,
            runtime={
                "thread_id": request.thread_id,
                "user_id": request.user_id,
                "scene": request.scene,
                "budget": asdict(budget_report),
            },
        )

    async def append_user_message(self, request: ContextRequest) -> ContextEvent | None:
        content = (request.message or "").strip()
        if not content:
            return None
        event = ContextEvent(
            id=str(uuid4()),
            thread_id=request.thread_id,
            type="message",
            role="user",
            content=content,
            payload={"scene": request.scene},
            token_estimate=self.token_counter.count_text(content),
        )
        return await self.conversation_store.append_event(event)

    async def append_assistant_message(
        self,
        *,
        thread_id: str,
        content: str,
        payload: dict[str, Any] | None = None,
    ) -> ContextEvent:
        event = ContextEvent(
            id=str(uuid4()),
            thread_id=thread_id,
            type="message",
            role="assistant",
            content=content,
            payload=payload or {},
            token_estimate=self.token_counter.count_text(content),
        )
        return await self.conversation_store.append_event(event)

    async def append_tool_result(
        self,
        *,
        thread_id: str,
        tool_name: str,
        content: str,
        payload: dict[str, Any],
        preview: Any,
    ) -> ContextEvent:
        event = ContextEvent(
            id=str(uuid4()),
            thread_id=thread_id,
            type="tool_result",
            role="tool",
            content=content,
            payload={
                "tool_name": tool_name,
                "payload": payload,
                "preview": preview,
            },
            token_estimate=self.token_counter.count_text(content),
        )
        return await self.conversation_store.append_event(event)

    async def _collect_blocks(self, request: ContextRequest) -> list[ContextBlock]:
        blocks: list[ContextBlock] = []
        for provider in self.providers:
            provider_blocks = await provider.collect(request)
            blocks.extend(provider_blocks)
        if request.context_overrides:
            blocks.append(
                ContextBlock(
                    kind="client_context",
                    source="client_context_overrides",
                    content=str(request.context_overrides),
                    priority=80,
                    metadata=request.context_overrides,
                )
            )
        return blocks

    async def _retrieve_memories(self, request: ContextRequest) -> list[MemoryRecord]:
        if not request.user_id or not request.message:
            return []
        return await self.memory_store.search(
            namespace=("user", request.user_id),
            query=request.message,
            top_k=5,
        )

    def _compose_messages(
        self,
        *,
        request: ContextRequest,
        events: list[ContextEvent],
        blocks: list[ContextBlock],
        budget_report: BudgetReport,
    ) -> list[Any]:
        system_parts = [request.system_prompt.strip() or "You are a helpful assistant."]
        visible_blocks = [
            block
            for block in sorted(blocks, key=lambda item: item.priority, reverse=True)
            if block.safe_to_send and block.kind not in set(budget_report.dropped_blocks)
        ]
        if visible_blocks:
            system_parts.append("<context_blocks>")
            for block in visible_blocks:
                system_parts.append(
                    f"<block kind=\"{block.kind}\" source=\"{block.source or ''}\">\n{block.content}\n</block>"
                )
            system_parts.append("</context_blocks>")

        messages: list[Any] = [SystemMessage(content="\n\n".join(part for part in system_parts if part))]
        dropped_event_ids = set(budget_report.dropped_event_ids)
        for event in events:
            if event.id in dropped_event_ids:
                continue
            if event.type == "condensation":
                messages.append(SystemMessage(content=event.content or ""))
            elif event.role == "user":
                messages.append(HumanMessage(content=event.content or ""))
            elif event.role == "assistant":
                messages.append(AIMessage(content=event.content or ""))
            elif event.role == "tool":
                tool_name = str(event.payload.get("tool_name") or "tool")
                messages.append(
                    ToolMessage(
                        content=event.content or "",
                        name=tool_name,
                        tool_call_id=str(event.payload.get("tool_call_id") or event.id),
                    )
                )
        current = (request.message or "").strip()
        if current:
            messages.append(HumanMessage(content=current))
        return messages

    @staticmethod
    def _render_memory(memory: MemoryRecord) -> str:
        return (
            f"source=memory id={memory.id} score={memory.score:.3f} "
            f"metadata={memory.metadata}\n{memory.content}"
        )

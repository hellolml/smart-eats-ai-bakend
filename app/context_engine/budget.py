from __future__ import annotations

from app.context_engine.tokenizer import ApproxTokenCounter
from app.context_engine.types import BudgetReport, ContextBlock
from app.context_engine.view import ViewBuilder


class BudgetManager:
    def __init__(
        self,
        *,
        token_counter: ApproxTokenCounter,
        max_tokens: int,
        soft_ratio: float = 0.7,
        hard_ratio: float = 0.85,
        condenser=None,
    ) -> None:
        self.token_counter = token_counter
        self.max_tokens = max_tokens
        self.soft_limit = int(max_tokens * soft_ratio)
        self.hard_limit = int(max_tokens * hard_ratio)
        self.condenser = condenser

    async def fit_thread(self, thread_id: str, blocks: list[ContextBlock], *, memory_namespace=None) -> BudgetReport:
        store = self.condenser.store if self.condenser is not None else None
        view = await ViewBuilder(store).build(thread_id) if store is not None else None
        events = view.events if view else []
        event_tokens = sum(self.token_counter.count_event(item) for item in events)
        block_tokens = sum(self.token_counter.count_block(item) for item in blocks if item.safe_to_send)
        total = event_tokens + block_tokens
        condensation_triggered = False
        if total > self.hard_limit and self.condenser is not None:
            condensation = await self.condenser.condense(thread_id, memory_namespace=memory_namespace)
            condensation_triggered = condensation is not None
            view = await ViewBuilder(store).build(thread_id)
            events = view.events
            event_tokens = sum(self.token_counter.count_event(item) for item in events)
            total = event_tokens + block_tokens

        dropped: list[str] = []
        kept_blocks = [block for block in blocks if block.safe_to_send]
        if total > self.max_tokens:
            keep: list[ContextBlock] = []
            for block in sorted(blocks, key=lambda item: item.priority, reverse=True):
                candidate_total = event_tokens + sum(self.token_counter.count_block(item) for item in keep) + self.token_counter.count_block(block)
                if candidate_total <= self.max_tokens:
                    keep.append(block)
                else:
                    dropped.append(block.kind)
            kept_blocks = keep
            block_tokens = sum(self.token_counter.count_block(item) for item in keep)
            total = event_tokens + block_tokens

        dropped_event_ids: list[str] = []
        if total > self.max_tokens and events:
            kept_events, dropped_event_ids = self._fit_events_head_tail(events, self.max_tokens - block_tokens)
            event_tokens = sum(self.token_counter.count_event(item) for item in kept_events)
            total = event_tokens + block_tokens

        return BudgetReport(
            total_tokens=min(total, self.max_tokens),
            max_tokens=self.max_tokens,
            buckets=self._bucket_tokens(events, kept_blocks, dropped_event_ids),
            dropped_blocks=dropped,
            dropped_event_ids=dropped_event_ids,
            condensation_triggered=condensation_triggered,
            status="truncated" if dropped_event_ids else ("condensed" if condensation_triggered else "ok"),
        )

    def _fit_events_head_tail(
        self,
        events,
        token_budget: int,
    ):
        if token_budget <= 0:
            return [], [event.id for event in events]
        head_count = min(2, len(events))
        kept = list(events[:head_count])
        used = sum(self.token_counter.count_event(item) for item in kept)
        dropped: list[str] = []
        for event in reversed(events[head_count:]):
            cost = self.token_counter.count_event(event)
            if used + cost <= token_budget:
                kept.insert(head_count, event)
                used += cost
            else:
                dropped.append(event.id)
        kept_ids = {event.id for event in kept}
        dropped.extend(event.id for event in events if event.id not in kept_ids and event.id not in dropped)
        return kept, dropped

    def _bucket_tokens(self, events, blocks: list[ContextBlock], dropped_event_ids: list[str]) -> dict[str, int]:
        buckets = {
            "system": 0,
            "current_user": 0,
            "recent_messages": 0,
            "tool_preview": 0,
            "memories": 0,
            "business_facts": 0,
            "summaries": 0,
        }
        dropped = set(dropped_event_ids)
        visible_events = [event for event in events if event.id not in dropped]
        user_events = [event for event in visible_events if event.role == "user"]
        latest_user_id = user_events[-1].id if user_events else None
        for event in visible_events:
            cost = self.token_counter.count_event(event)
            if event.type == "condensation":
                buckets["summaries"] += cost
            elif event.type == "tool_result" or event.role == "tool":
                buckets["tool_preview"] += cost
            elif event.id == latest_user_id:
                buckets["current_user"] += cost
            else:
                buckets["recent_messages"] += cost
        for block in blocks:
            cost = self.token_counter.count_block(block)
            if block.kind in {"memory", "memories"}:
                buckets["memories"] += cost
            elif block.kind == "business_facts":
                buckets["business_facts"] += cost
            elif block.kind == "system":
                buckets["system"] += cost
            else:
                buckets["system"] += cost
        return buckets

from __future__ import annotations

from dataclasses import dataclass

from app.context_engine.types import ContextEvent


@dataclass(slots=True)
class ContextView:
    events: list[ContextEvent]
    covered_event_ids: set[str]


class ViewBuilder:
    def __init__(self, store) -> None:
        self.store = store

    async def build(self, thread_id: str) -> ContextView:
        events = await self.store.list_events(thread_id)
        condensations = [
            item
            for item in await self.store.list_condensations(thread_id)
            if item.status == "completed" and item.covered_event_ids
        ]
        if not condensations:
            return ContextView(events=events, covered_event_ids=set())

        covered: set[str] = set()
        summaries_by_offset: dict[int, list[ContextEvent]] = {}
        id_to_index = {event.id: idx for idx, event in enumerate(events)}
        for condensation in condensations:
            ids = [event_id for event_id in condensation.covered_event_ids if event_id in id_to_index]
            if not ids:
                continue
            covered.update(ids)
            first_id = ids[0]
            last_id = ids[-1]
            offset = id_to_index[first_id]
            summaries_by_offset.setdefault(offset, []).append(
                ContextEvent(
                    id=f"summary:{first_id}:{last_id}",
                    thread_id=thread_id,
                    type="condensation",
                    role="system",
                    content=condensation.summary,
                    payload={
                        "summary": condensation.summary,
                        "summary_json": condensation.summary_json,
                        "covered_event_ids": ids,
                    },
                )
            )

        view: list[ContextEvent] = []
        for idx, event in enumerate(events):
            if idx in summaries_by_offset:
                view.extend(summaries_by_offset[idx])
            if event.id in covered:
                continue
            view.append(event)
        return ContextView(events=view, covered_event_ids=covered)

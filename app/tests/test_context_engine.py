from __future__ import annotations

import pytest

from app.context_engine.budget import BudgetManager
from app.context_engine.condenser import Condenser
from app.context_engine.memory import InMemoryVectorMemoryStore
from app.context_engine.stores import InMemoryConversationStore
from app.context_engine.tokenizer import ApproxTokenCounter
from app.context_engine.types import ContextBlock, ContextEvent, ContextRequest
from app.context_engine.view import ViewBuilder


def _event(event_id: str, content: str, event_type: str = "message", role: str = "user") -> ContextEvent:
    return ContextEvent(
        id=event_id,
        thread_id="t1",
        type=event_type,
        role=role,
        content=content,
    )


@pytest.mark.asyncio
async def test_view_builder_replaces_covered_events_with_condensation_summary():
    store = InMemoryConversationStore()
    events = [_event(f"e{i}", f"消息 {i}") for i in range(1, 8)]
    for item in events:
        await store.append_event(item)
    await store.save_condensation(
        thread_id="t1",
        summary="中间消息摘要",
        summary_json={"summary": "中间消息摘要"},
        covered_event_ids=["e3", "e4", "e5"],
        summary_offset=2,
        status="completed",
    )

    view = await ViewBuilder(store).build("t1")

    assert [item.id for item in view.events] == ["e1", "e2", "summary:e3:e5", "e6", "e7"]
    assert view.events[2].type == "condensation"
    assert view.events[2].content == "中间消息摘要"


@pytest.mark.asyncio
async def test_condenser_selects_uncovered_contiguous_middle_segment():
    store = InMemoryConversationStore()
    for idx in range(1, 15):
        await store.append_event(_event(f"e{idx}", f"消息 {idx}"))
    await store.save_condensation(
        thread_id="t1",
        summary="旧摘要",
        summary_json={"summary": "旧摘要"},
        covered_event_ids=["e3", "e4", "e5", "e6"],
        summary_offset=2,
        status="completed",
    )
    condenser = Condenser(
        store=store,
        token_counter=ApproxTokenCounter(),
        summarizer=lambda events, previous=None: {"summary": "新摘要"},
        keep_head=2,
        keep_tail=4,
        min_events=2,
    )

    condensation = await condenser.condense("t1")

    assert condensation is not None
    assert condensation.covered_event_ids == ["e7", "e8", "e9", "e10"]
    view = await ViewBuilder(store).build("t1")
    assert [item.id for item in view.events] == [
        "e1",
        "e2",
        "summary:e3:e6",
        "summary:e7:e10",
        "e11",
        "e12",
        "e13",
        "e14",
    ]


@pytest.mark.asyncio
async def test_budget_manager_triggers_condensation_when_hard_limit_exceeded():
    store = InMemoryConversationStore()
    for idx in range(1, 12):
        await store.append_event(_event(f"e{idx}", "很长的上下文内容" * 20))
    condenser = Condenser(
        store=store,
        token_counter=ApproxTokenCounter(),
        summarizer=lambda events, previous=None: {"summary": "压缩摘要"},
        keep_head=1,
        keep_tail=3,
        min_events=2,
    )
    manager = BudgetManager(
        token_counter=ApproxTokenCounter(),
        max_tokens=80,
        soft_ratio=0.5,
        hard_ratio=0.8,
        condenser=condenser,
    )

    report = await manager.fit_thread("t1", [ContextBlock(kind="system", content="系统规则")])

    assert report.condensation_triggered is True
    assert report.total_tokens <= 80
    condensations = await store.list_condensations("t1")
    assert len(condensations) == 1
    assert condensations[0].status == "completed"


@pytest.mark.asyncio
async def test_in_memory_vector_store_filters_namespace_and_metadata():
    store = InMemoryVectorMemoryStore()
    await store.put(
        namespace=("user", "u1"),
        content="喜欢清淡口味",
        metadata={"type": "preference", "scene": "food"},
    )
    await store.put(
        namespace=("user", "u2"),
        content="喜欢重辣",
        metadata={"type": "preference", "scene": "food"},
    )
    await store.put(
        namespace=("user", "u1"),
        content="上次路线目的地是五一广场",
        metadata={"type": "route", "scene": "travel"},
    )

    results = await store.search(
        namespace=("user", "u1"),
        query="清淡晚餐",
        top_k=5,
        metadata_filter={"type": "preference"},
    )

    assert [item.content for item in results] == ["喜欢清淡口味"]
    assert results[0].score > 0


@pytest.mark.asyncio
async def test_context_engine_prepare_returns_native_messages():
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.context_engine.engine import ContextEngine

    store = InMemoryConversationStore()
    memory = InMemoryVectorMemoryStore()
    engine = ContextEngine(
        conversation_store=store,
        memory_store=memory,
        token_counter=ApproxTokenCounter(),
        providers=[],
    )

    prepared = await engine.prepare(
        ContextRequest(
            thread_id="t-native",
            user_id="u1",
            message="今天晚饭吃什么？",
            scene="chat",
            system_prompt="你是助手",
        )
    )

    assert isinstance(prepared.messages[0], SystemMessage)
    assert isinstance(prepared.messages[-1], HumanMessage)
    assert prepared.messages[-1].content == "今天晚饭吃什么？"
    assert prepared.budget_report.total_tokens > 0


@pytest.mark.asyncio
async def test_structured_condensation_renders_segment_scope_and_metrics():
    from app.context_engine.memory_extractor import MemoryPolicy
    from app.context_engine.renderers import render_condensation_summary

    store = InMemoryConversationStore()
    memory = InMemoryVectorMemoryStore()
    for idx in range(1, 10):
        await store.append_event(_event(f"e{idx}", f"用户偏好和任务片段 {idx}"))

    condenser = Condenser(
        store=store,
        token_counter=ApproxTokenCounter(),
        summarizer=lambda events, previous=None: {
            "segment_summary": "用户在这段历史中确认想吃清淡晚餐。",
            "stable_preferences": ["用户偏好清淡口味"],
            "task_state_at_segment_end": ["已完成偏好确认，但这不是全局最新状态"],
            "memory_candidates": [
                {
                    "kind": "preference",
                    "content": "用户偏好清淡口味",
                    "confidence": 0.92,
                    "ttl": "none",
                    "source_event_ids": [events[0].id],
                }
            ],
        },
        keep_head=2,
        keep_tail=2,
        min_events=2,
        memory_store=memory,
        memory_policy=MemoryPolicy(),
    )

    condensation = await condenser.condense("t1", memory_namespace=("user", "u1"))

    assert condensation is not None
    assert condensation.summary_json["segment_summary"] == "用户在这段历史中确认想吃清淡晚餐。"
    rendered = render_condensation_summary(condensation.summary_json)
    assert 'scope="historical_middle_segment"' in rendered
    assert "Newer raw messages after this summary are authoritative" in rendered
    runs = await store.list_compaction_runs("t1")
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].compression_ratio > 0
    memories = await memory.search(namespace=("user", "u1"), query="清淡", metadata_filter={"kind": "preference"})
    assert [item.content for item in memories] == ["用户偏好清淡口味"]


@pytest.mark.asyncio
async def test_failed_condensation_records_metric_without_covering_events():
    store = InMemoryConversationStore()
    for idx in range(1, 8):
        await store.append_event(_event(f"e{idx}", f"消息 {idx}"))

    def _broken(_events, previous=None):
        raise RuntimeError("model timeout")

    condenser = Condenser(
        store=store,
        token_counter=ApproxTokenCounter(),
        summarizer=_broken,
        keep_head=1,
        keep_tail=1,
        min_events=2,
    )

    condensation = await condenser.condense("t1")

    assert condensation is not None
    assert condensation.status == "failed"
    assert condensation.covered_event_ids == []
    view = await ViewBuilder(store).build("t1")
    assert [item.id for item in view.events] == [f"e{idx}" for idx in range(1, 8)]
    runs = await store.list_compaction_runs("t1")
    assert runs[0].status == "failed"
    assert runs[0].error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_source_event_retriever_searches_original_events_even_when_summarized():
    from app.context_engine.source_events import SourceEventRetriever

    store = InMemoryConversationStore()
    await store.append_event(_event("e1", "开场"))
    await store.append_event(_event("e2", "工具返回路线包含五一广场和橘子洲", "tool_result", "tool"))
    await store.append_event(_event("e3", "尾部最新消息"))
    await store.save_condensation(
        thread_id="t1",
        summary="路线摘要",
        summary_json={"segment_summary": "路线摘要"},
        covered_event_ids=["e2"],
        summary_offset=1,
        status="completed",
    )

    hits = await SourceEventRetriever(store).search_events(
        namespace=("thread", "t1"),
        thread_id="t1",
        query="五一广场",
        top_k=3,
    )

    assert [hit.event_id for hit in hits] == ["e2"]
    assert hits[0].content_preview.startswith("工具返回路线")


@pytest.mark.asyncio
async def test_agentic_memory_service_write_search_update_forget():
    from app.context_engine.agentic_memory import AgenticMemoryService
    from app.context_engine.memory_extractor import MemoryPolicy

    memory = InMemoryVectorMemoryStore()
    service = AgenticMemoryService(memory_store=memory, policy=MemoryPolicy())

    written = await service.memory_write(
        namespace=("user", "u1"),
        content="用户不吃香菜",
        kind="constraint",
        source="user_explicit",
        confidence=0.95,
    )
    assert written["status"] == "written"

    results = await service.memory_search(namespace=("user", "u1"), query="香菜", top_k=5)
    assert results[0]["content"] == "用户不吃香菜"

    updated = await service.memory_update(
        namespace=("user", "u1"),
        memory_id=results[0]["id"],
        content="用户现在可以接受少量香菜",
    )
    assert updated["status"] == "updated"
    forgotten = await service.memory_forget(namespace=("user", "u1"), memory_id=results[0]["id"])
    assert forgotten["status"] == "deleted"
    assert await service.memory_search(namespace=("user", "u1"), query="香菜", top_k=5) == []

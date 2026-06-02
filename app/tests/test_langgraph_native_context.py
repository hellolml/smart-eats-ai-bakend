from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.modifier import RemoveMessage
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore

from app.agent.langgraph_context import (
    build_active_context_report,
    build_model_messages,
    build_summary_update,
    detect_compact_thrash,
    normalize_summary_output,
    persist_summary_memories,
    load_user_memories,
    parse_model_context_windows,
    resolve_model_context_window,
    save_source_event,
    search_source_events,
    should_summarize_context,
    tier_tool_messages,
)
from app.agent.tools import context_memory


@pytest.mark.asyncio
async def test_build_model_messages_injects_context_without_mutating_state_messages():
    state_messages = [HumanMessage(content="今晚想吃辣的")]
    memories = [{"id": "m1", "content": "用户喜欢川菜", "score": 0.8}]

    model_messages = build_model_messages(
        system_prompt="你是通用助手。",
        summary="用户正在找晚饭。",
        messages=state_messages,
        memories=memories,
    )

    assert isinstance(model_messages[0], SystemMessage)
    assert "用户喜欢川菜" in model_messages[0].content
    assert isinstance(model_messages[1], SystemMessage)
    assert "用户正在找晚饭" in model_messages[1].content
    assert model_messages[-1] is state_messages[0]
    assert state_messages == [HumanMessage(content="今晚想吃辣的")]


def test_resolve_model_context_window_uses_model_specific_overrides():
    overrides = parse_model_context_windows("qwen:qwen3.5-plus=131072,gpt-4.1=1047576")

    assert resolve_model_context_window(provider="qwen", model="qwen3.5-plus", overrides=overrides) == 131072
    assert resolve_model_context_window(provider="openai", model="gpt-4.1", overrides=overrides) == 1047576
    assert resolve_model_context_window(provider="unknown", model="missing", fallback=128000) == 128000


def test_active_context_report_counts_all_runtime_buckets_and_reserves_output():
    messages = [HumanMessage(content="今晚想吃辣的" * 100, id="h1")]
    report = build_active_context_report(
        system_prompt="系统提示" * 100,
        messages=messages,
        summary="旧摘要" * 100,
        memories=[{"content": "用户不吃香菜" * 50}],
        model_context_window=2000,
        trigger_ratio=0.5,
        hard_ratio=0.8,
        reserved_output_tokens=200,
        reserved_tool_tokens=100,
    )

    assert report["buckets"]["system"] > 0
    assert report["buckets"]["messages"] > 0
    assert report["buckets"]["summary"] > 0
    assert report["buckets"]["memories"] > 0
    assert report["usable_context_window"] == 1700
    assert report["soft_limit"] == 850
    assert report["hard_limit"] == 1360
    assert report["total_tokens"] == sum(report["buckets"].values())
    assert should_summarize_context(report, min_messages=1) is True


def test_tier_tool_messages_archives_older_tool_previews():
    messages = [
        ToolMessage(content="old result " * 100, id="t1", name="search_restaurants", tool_call_id="call_1"),
        HumanMessage(content="继续", id="h1"),
        ToolMessage(content="recent result", id="t2", name="plan_route", tool_call_id="call_2"),
    ]

    tiered = tier_tool_messages(messages, keep_recent_tool_messages=1, max_tool_preview_chars=40)

    assert isinstance(tiered[0], ToolMessage)
    assert tiered[0].id == "t1"
    archived_payload = json.loads(tiered[0].content)
    assert archived_payload["tier"] == "archived_tool_preview"
    assert archived_payload["tool_name"] == "search_restaurants"
    assert len(archived_payload["content_preview"]) <= 40
    assert tiered[-1] is messages[-1]


def test_detect_compact_thrash_blocks_repeated_low_value_compactions():
    report = {"total_tokens": 950, "hard_limit": 900}
    previous = {"compact_attempts": 2, "last_compaction_reduction_ratio": 0.01}

    decision = detect_compact_thrash(previous, report, max_attempts=2, min_reduction_ratio=0.05)

    assert decision["blocked"] is True
    assert decision["reason"] == "low_value_repeated_compaction"


def test_build_summary_update_removes_old_messages_but_keeps_recent_tool_pair():
    messages = [
        HumanMessage(content="1", id="h1"),
        AIMessage(content="2", id="a1"),
        HumanMessage(content="3", id="h2"),
        AIMessage(
            content="",
            id="a2",
            tool_calls=[{"name": "search_restaurants", "args": {}, "id": "call_1", "type": "tool_call"}],
        ),
        ToolMessage(content="preview", id="t1", name="search_restaurants", tool_call_id="call_1"),
        HumanMessage(content="继续", id="h3"),
    ]

    update = build_summary_update(
        messages,
        previous_summary="旧摘要",
        new_summary="新摘要",
        keep_recent=3,
    )

    removed_ids = [item.id for item in update["messages"] if isinstance(item, RemoveMessage)]
    assert removed_ids == ["h1", "a1", "h2"]
    assert update["summary"] == "新摘要"
    assert update["context_budget"]["removed_message_count"] == 3


def test_build_summary_update_removes_complete_turns_and_records_covered_ids():
    messages = [
        HumanMessage(content="turn1 user", id="h1"),
        AIMessage(content="turn1 assistant", id="a1"),
        HumanMessage(content="turn2 user", id="h2"),
        AIMessage(content="turn2 assistant", id="a2"),
        HumanMessage(content="turn3 user", id="h3"),
        AIMessage(content="turn3 assistant", id="a3"),
        HumanMessage(content="turn4 user", id="h4"),
        AIMessage(content="turn4 assistant", id="a4"),
    ]

    update = build_summary_update(
        messages,
        previous_summary=None,
        new_summary="新摘要",
        keep_recent=4,
        keep_recent_turns=2,
        source_refs=[{"event_id": "src-1", "message_id": "a2"}],
    )

    removed_ids = [item.id for item in update["messages"] if isinstance(item, RemoveMessage)]
    assert removed_ids == ["h1", "a1", "h2", "a2"]
    assert update["context_budget"]["covered_message_ids"] == removed_ids
    assert update["context_budget"]["source_refs"] == [{"event_id": "src-1", "message_id": "a2"}]


def test_normalize_summary_output_enforces_schema_from_json_fence():
    raw = """
    ```json
    {
      "summary": "用户正在找晚餐。",
      "stable_preferences": ["用户不吃香菜"],
      "decisions": ["优先推荐附近餐厅"]
    }
    ```
    """

    normalized = normalize_summary_output(raw)

    assert normalized["valid"] is True
    assert normalized["summary_json"]["summary"] == "用户正在找晚餐。"
    assert normalized["summary_json"]["stable_preferences"] == ["用户不吃香菜"]
    assert normalized["summary_json"]["tool_results"] == []
    assert normalized["summary_json"]["task_state"]["stage"] == "unknown"
    assert normalized["summary_json"]["coverage"]["covered_message_ids"] == []
    assert "stable_preferences" in normalized["summary"]


def test_normalize_summary_output_repairs_plain_text_into_schema():
    normalized = normalize_summary_output("用户想吃辣，路线规划还没有完成。")

    assert normalized["valid"] is False
    assert normalized["summary_json"]["summary"] == "用户想吃辣，路线规划还没有完成。"
    assert normalized["summary_json"]["open_questions"] == []


@pytest.mark.asyncio
async def test_persist_summary_memories_writes_high_confidence_stable_preferences():
    store = InMemoryStore()
    summary_json = {
        "stable_preferences": [
            {"content": "用户不吃香菜", "confidence": 0.9},
            {"content": "本轮临时想吃火锅", "confidence": 0.5},
        ]
    }

    written = await persist_summary_memories(store, user_id="u1", summary_json=summary_json)
    memories = await load_user_memories(store, user_id="u1", query="香菜", limit=5)

    assert len(written) == 1
    assert memories[0]["content"] == "用户不吃香菜"
    assert memories[0]["kind"] == "stable_preference"


@pytest.mark.asyncio
async def test_memory_tools_use_langgraph_store_namespace():
    store = InMemoryStore()
    runtime = {"langgraph_store": store, "user_id": "u1"}
    node = ToolNode(
        [
            context_memory.memory_write_tool,
            context_memory.memory_search_tool,
            context_memory.memory_update_tool,
            context_memory.memory_forget_tool,
        ],
        messages_key="messages",
    )

    written = await context_memory.memory_write_tool.ainvoke(
        {
            "content": "用户不吃香菜",
            "kind": "preference",
            "confidence": 0.9,
            "runtime_context": runtime,
        }
    )
    tool_output = await node.ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory_search",
                            "args": {"query": "香菜", "top_k": 3},
                            "id": "call_memory_search",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "runtime_context": runtime,
        }
    )
    hits = json.loads(tool_output["messages"][0].content)

    assert written["memory_id"]
    assert hits[0]["content"] == "用户不吃香菜"
    assert hits[0]["namespace"] == ["memories", "u1"]

    updated = await context_memory.memory_update_tool.ainvoke(
        {
            "memory_id": written["memory_id"],
            "content": "用户少吃香菜",
            "runtime_context": runtime,
        }
    )
    assert updated["content"] == "用户少吃香菜"

    forgotten = await context_memory.memory_forget_tool.ainvoke(
        {"memory_id": written["memory_id"], "runtime_context": runtime}
    )
    assert forgotten["deleted"] is True
    assert await context_memory.memory_search_tool.ainvoke(
        {"query": "香菜", "top_k": 3, "runtime_context": runtime}
    ) == []


@pytest.mark.asyncio
async def test_source_event_search_uses_langgraph_store():
    store = InMemoryStore()
    await save_source_event(
        store,
        thread_id="s1",
        tool_name="search_restaurants",
        tool_call_id="call_1",
        args={"query": "火锅"},
        result={"restaurants": [{"name": "山城火锅"}]},
        preview={"names": ["山城火锅"]},
    )

    hits = await search_source_events(store, thread_id="s1", query="火锅", top_k=5)

    assert hits[0]["tool_name"] == "search_restaurants"
    assert hits[0]["content_preview"]
    assert hits[0]["metadata"]["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_load_user_memories_returns_sanitized_records():
    store = InMemoryStore()
    await store.aput(
        ("memories", "u1"),
        "m1",
        {"content": "用户喜欢步行可达的餐厅", "kind": "preference", "confidence": 0.8},
    )

    memories = await load_user_memories(store, user_id="u1", query="餐厅", limit=3)

    assert memories == [
        {
            "id": "m1",
            "namespace": ["memories", "u1"],
            "content": "用户喜欢步行可达的餐厅",
            "kind": "preference",
            "confidence": 0.8,
            "score": None,
        }
    ]

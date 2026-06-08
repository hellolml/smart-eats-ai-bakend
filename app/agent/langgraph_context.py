from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.messages.modifier import RemoveMessage


DEFAULT_MODEL_CONTEXT_WINDOW = 128_000
DEFAULT_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-5": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5-nano": 400_000,
    "qwen3.5-flash": 128_000,
    "qwen3.5-plus": 128_000,
    "deepseek-chat": 64_000,
}


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_count = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_count = len(text) - ascii_count
    return int(ascii_count / 4) + non_ascii_count + 4


def estimate_messages_tokens(messages: list[Any]) -> int:
    total = 0
    for message in messages:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
        else:
            total += estimate_text_tokens(str(content))
    return total


def parse_model_context_windows(raw: str | None) -> dict[str, int]:
    windows: dict[str, int] = {}
    if not raw:
        return windows
    for item in raw.split(","):
        chunk = item.strip()
        if not chunk:
            continue
        if "=" in chunk:
            key, value = chunk.split("=", 1)
        elif ":" in chunk:
            parts = chunk.rsplit(":", 1)
            if len(parts) != 2:
                continue
            key, value = parts
        else:
            continue
        key = key.strip().lower()
        try:
            window = int(value.strip())
        except ValueError:
            continue
        if key and window > 0:
            windows[key] = window
    return windows


def resolve_model_context_window(
    *,
    provider: str | None = None,
    model: str | None = None,
    fallback: int = DEFAULT_MODEL_CONTEXT_WINDOW,
    overrides: dict[str, int] | None = None,
) -> int:
    provider_key = str(provider or "").strip().lower()
    model_key = str(model or "").strip().lower()
    lookup = {**DEFAULT_MODEL_CONTEXT_WINDOWS, **(overrides or {})}
    candidates = []
    if provider_key and model_key:
        candidates.append(f"{provider_key}:{model_key}")
    if model_key:
        candidates.append(model_key)
    if provider_key:
        candidates.append(provider_key)
    for key in candidates:
        value = lookup.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return fallback if fallback > 0 else DEFAULT_MODEL_CONTEXT_WINDOW


def _memory_tokens(memories: list[dict[str, Any]] | None) -> int:
    total = 0
    for item in memories or []:
        total += estimate_text_tokens(str(item.get("content") or ""))
        total += estimate_text_tokens(str(item.get("kind") or ""))
    return total


def build_active_context_report(
    *,
    system_prompt: str,
    messages: list[Any],
    summary: str | None = None,
    memories: list[dict[str, Any]] | None = None,
    model_context_window: int,
    trigger_ratio: float,
    hard_ratio: float = 0.92,
    reserved_output_tokens: int = 8_000,
    reserved_tool_tokens: int = 16_000,
    business_context_tokens: int = 0,
) -> dict[str, Any]:
    reserves = {
        "output": max(0, int(reserved_output_tokens)),
        "tool": max(0, int(reserved_tool_tokens)),
    }
    usable_window = max(1, int(model_context_window) - sum(reserves.values()))
    buckets = {
        "system": estimate_text_tokens(system_prompt or ""),
        "summary": estimate_text_tokens(summary or ""),
        "messages": estimate_messages_tokens(messages),
        "memories": _memory_tokens(memories),
        "business_context": max(0, int(business_context_tokens)),
    }
    total_tokens = sum(buckets.values())
    soft_limit = int(usable_window * trigger_ratio)
    hard_limit = int(usable_window * hard_ratio)
    return {
        "model_context_window": int(model_context_window),
        "usable_context_window": usable_window,
        "reserved_tokens": reserves,
        "buckets": buckets,
        "total_tokens": total_tokens,
        "message_count": len(messages),
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
        "trigger_ratio": trigger_ratio,
        "hard_ratio": hard_ratio,
        "should_compact": total_tokens >= soft_limit,
        "over_hard_limit": total_tokens >= hard_limit,
    }


def should_summarize_context(
    report: dict[str, Any],
    *,
    min_messages: int,
    previous_budget: dict[str, Any] | None = None,
) -> bool:
    if (previous_budget or {}).get("compact_blocked"):
        return False
    if int(report.get("message_count") or 0) < min_messages:
        return False
    return bool(report.get("should_compact"))


def detect_compact_thrash(
    previous_budget: dict[str, Any] | None,
    active_report: dict[str, Any],
    *,
    max_attempts: int = 2,
    min_reduction_ratio: float = 0.05,
) -> dict[str, Any]:
    previous = previous_budget or {}
    attempts = int(previous.get("compact_attempts") or 0)
    reduction = float(previous.get("last_compaction_reduction_ratio") or 0.0)
    over_limit = int(active_report.get("total_tokens") or 0) >= int(active_report.get("hard_limit") or 0)
    blocked = over_limit and attempts >= max_attempts and reduction < min_reduction_ratio
    return {
        "blocked": blocked,
        "reason": "low_value_repeated_compaction" if blocked else None,
        "attempts": attempts,
        "last_compaction_reduction_ratio": reduction,
    }


def tier_tool_messages(
    messages: list[Any],
    *,
    keep_recent_tool_messages: int,
    max_tool_preview_chars: int,
) -> list[Any]:
    from langchain_core.messages import ToolMessage

    tool_indexes = [index for index, message in enumerate(messages) if isinstance(message, ToolMessage)]
    keep = set(tool_indexes[-max(0, keep_recent_tool_messages):])
    tiered: list[Any] = []
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage) or index in keep:
            tiered.append(message)
            continue
        content = str(getattr(message, "content", "") or "")
        payload = {
            "tier": "archived_tool_preview",
            "tool_name": getattr(message, "name", None),
            "tool_call_id": getattr(message, "tool_call_id", None),
            "content_preview": content[: max(0, max_tool_preview_chars)],
            "retrieval_hint": "Full tool result can be searched with source_event_search.",
        }
        tiered.append(
            ToolMessage(
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                name=getattr(message, "name", None),
                tool_call_id=str(getattr(message, "tool_call_id", None) or ""),
                id=getattr(message, "id", None),
            )
        )
    return tiered


def build_model_messages(
    *,
    system_prompt: str,
    messages: list[Any],
    summary: str | None = None,
    memories: list[dict[str, Any]] | None = None,
    runtime_context_prompt: str | None = None,
) -> list[Any]:
    stable_system = system_prompt.strip() or "You are a helpful assistant."
    memory_lines = []
    for item in memories or []:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        memory_id = item.get("id")
        kind = item.get("kind")
        confidence = item.get("confidence")
        memory_lines.append(f"- id={memory_id} kind={kind} confidence={confidence}: {content}")

    model_messages: list[Any] = [SystemMessage(content=stable_system)]
    if runtime_context_prompt and runtime_context_prompt.strip():
        model_messages.append(SystemMessage(content=runtime_context_prompt.strip()))
    if memory_lines:
        model_messages.append(SystemMessage(content="<long_term_memories>\n" + "\n".join(memory_lines) + "\n</long_term_memories>"))
    if summary and summary.strip():
        model_messages.append(
            SystemMessage(
                content=(
                    "<conversation_summary>\n"
                    f"{summary.strip()}\n"
                    "</conversation_summary>\n"
                    "Recent raw messages after this summary are authoritative."
                )
            )
        )
    model_messages.extend(messages)
    return model_messages


def build_summary_prompt(
    *,
    previous_summary: str | None,
    messages: list[Any],
) -> str:
    formatted = []
    for index, message in enumerate(messages, start=1):
        role = getattr(message, "type", None) or message.__class__.__name__
        content = getattr(message, "content", "")
        formatted.append(f"[{index}] {role}: {content}")
    previous = previous_summary.strip() if previous_summary else "无"
    return (
        "你在为 skill-based agent runtime 生成 Claude-Code-like working-state compact summary。\n"
        "下一个模型看不到被压缩的旧消息，只能看到你的 JSON、保留的最近原文消息、长期记忆和可检索 source refs。\n"
        "请把下面旧对话压缩为严格 JSON 对象。只总结旧消息，不要虚构最新状态。\n"
        "必须只输出 JSON，不要 Markdown，不要解释。\n"
        "JSON 字段必须包含：summary, latest_user_intent, task_state, user_goals, stable_preferences, "
        "user_preferences, decisions, tool_results, open_questions, next_steps, avoid_repeating, "
        "current_task_state, coverage。\n"
        "字段含义：\n"
        "- summary: 旧消息段的工作状态总览，面向继续执行任务的模型。\n"
        "- latest_user_intent: 旧消息段内最后明确出现的用户意图；不要覆盖后续未压缩消息。\n"
        "- task_state: 对象，包含 stage、next_action、blocked_by。\n"
        "- user_goals: 用户在旧消息段中表达过的目标。\n"
        "- stable_preferences: 可长期复用且高置信的稳定偏好；临时想法不要放这里。\n"
        "- user_preferences: 偏好对象数组，包含 content、scope(long_term/session_only)、confidence、evidence。\n"
        "- decisions: 对象数组，保留已确认/暂定/已废弃的选择、约束或结论及 evidence。\n"
        "- tool_results: 对象数组，保留 tool_name、tool_call_id、source_event_id、key_facts、error。\n"
        "- open_questions: 对象数组，旧消息段结束时仍未解决的问题、blocked_by、ask_user。\n"
        "- next_steps: 对象数组，继续任务最应该做的动作、原因和优先级。\n"
        "- avoid_repeating: 对象数组，已经完成/失败/用户拒绝的动作，避免重复。\n"
        "- current_task_state: 旧消息段结束时的任务状态；不要覆盖后续最新消息。\n\n"
        "- coverage: 对象，包含 covered_message_ids、covered_source_event_ids、authoritative_tail_starts_after。\n"
        f"已有摘要：\n{previous}\n\n"
        "待压缩消息：\n"
        + "\n".join(formatted)
    )


SUMMARY_SCHEMA_FIELDS = (
    "summary",
    "latest_user_intent",
    "task_state",
    "user_goals",
    "stable_preferences",
    "user_preferences",
    "decisions",
    "tool_results",
    "open_questions",
    "next_steps",
    "avoid_repeating",
    "current_task_state",
    "coverage",
)

SUMMARY_LIST_FIELDS = (
    "user_goals",
    "stable_preferences",
    "user_preferences",
    "decisions",
    "tool_results",
    "open_questions",
    "next_steps",
    "avoid_repeating",
)

SUMMARY_OBJECT_FIELDS = (
    "task_state",
    "coverage",
)


def build_summary_repair_prompt(*, raw_output: str, original_prompt: str) -> str:
    return (
        "上一次摘要输出不是合法的目标 JSON。请根据原始压缩任务重新输出严格 JSON 对象。\n"
        "只能输出 JSON，不要 Markdown，不要解释。必须包含字段："
        + ", ".join(SUMMARY_SCHEMA_FIELDS)
        + "\n\n原始压缩任务：\n"
        + original_prompt
        + "\n\n上一次输出：\n"
        + raw_output
    )


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _extract_json_object(text: str) -> str:
    stripped = _strip_json_fence(text)
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def _coerce_summary_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value]


def _coerce_summary_object(value: Any, *, defaults: dict[str, Any]) -> dict[str, Any]:
    base = dict(defaults)
    if isinstance(value, dict):
        base.update(value)
    return base


def normalize_summary_output(raw_output: str) -> dict[str, Any]:
    raw = str(raw_output or "").strip()
    parsed: dict[str, Any]
    valid = True
    error: str | None = None
    try:
        loaded = json.loads(_extract_json_object(raw))
        if not isinstance(loaded, dict):
            raise ValueError("summary output is not a JSON object")
        parsed = loaded
    except Exception as exc:
        valid = False
        error = exc.__class__.__name__
        parsed = {"summary": raw[:1600]}

    summary_json: dict[str, Any] = {}
    for field in SUMMARY_SCHEMA_FIELDS:
        value = parsed.get(field)
        if field in SUMMARY_LIST_FIELDS:
            summary_json[field] = _coerce_summary_list(value)
        elif field in SUMMARY_OBJECT_FIELDS:
            defaults = (
                {"stage": "unknown", "next_action": "", "blocked_by": None}
                if field == "task_state"
                else {
                    "covered_message_ids": [],
                    "covered_source_event_ids": [],
                    "authoritative_tail_starts_after": "",
                }
            )
            summary_json[field] = _coerce_summary_object(value, defaults=defaults)
        else:
            summary_json[field] = str(value or "").strip()

    if not summary_json.get("latest_user_intent") and summary_json.get("user_goals"):
        summary_json["latest_user_intent"] = str(summary_json["user_goals"][-1])
    if not summary_json["summary"]:
        for field in SUMMARY_LIST_FIELDS:
            if summary_json[field]:
                summary_json["summary"] = "；".join(str(item) for item in summary_json[field][:3])
                break
    if not summary_json["summary"]:
        summary_json["summary"] = raw[:1600]

    return {
        "valid": valid,
        "error": error,
        "summary_json": summary_json,
        "summary": json.dumps(summary_json, ensure_ascii=False, sort_keys=True),
    }


def should_summarize(
    messages: list[Any],
    *,
    max_tokens: int,
    trigger_ratio: float,
    min_messages: int,
) -> bool:
    if len(messages) < min_messages:
        return False
    return estimate_messages_tokens(messages) >= int(max_tokens * trigger_ratio)


def _message_id(message: Any, fallback: str) -> str:
    value = getattr(message, "id", None)
    if isinstance(value, str) and value:
        return value
    return fallback


def _message_role(message: Any) -> str:
    role = getattr(message, "type", None) or getattr(message, "role", None)
    if isinstance(role, str):
        return role
    name = message.__class__.__name__.lower()
    if "human" in name:
        return "human"
    if "tool" in name:
        return "tool"
    if "ai" in name or "assistant" in name:
        return "ai"
    return name


def _recent_turn_start(messages: list[Any], keep_recent_turns: int | None) -> int | None:
    if not keep_recent_turns:
        return None
    user_indexes = [index for index, message in enumerate(messages) if _message_role(message) == "human"]
    if len(user_indexes) <= keep_recent_turns:
        return 0
    return user_indexes[-keep_recent_turns]


def _protected_recent_start(
    messages: list[Any],
    keep_recent: int,
    *,
    keep_recent_turns: int | None = None,
) -> int:
    if not messages:
        return 0
    start = max(0, len(messages) - keep_recent)
    turn_start = _recent_turn_start(messages, keep_recent_turns)
    if turn_start is not None:
        start = min(start, turn_start)
    tool_call_ids: set[str] = set()
    for message in messages[start:]:
        for call in getattr(message, "tool_calls", None) or []:
            call_id = call.get("id") if isinstance(call, dict) else None
            if isinstance(call_id, str):
                tool_call_ids.add(call_id)
        tool_call_id = getattr(message, "tool_call_id", None)
        if isinstance(tool_call_id, str):
            tool_call_ids.add(tool_call_id)

    if not tool_call_ids:
        return start
    for index in range(start - 1, -1, -1):
        message = messages[index]
        calls = getattr(message, "tool_calls", None) or []
        if any(isinstance(call, dict) and call.get("id") in tool_call_ids for call in calls):
            start = index
    return start


def _covered_source_refs(source_refs: list[dict[str, Any]] | None, removable: list[Any]) -> list[dict[str, Any]]:
    if not source_refs:
        return []
    covered_message_ids = {
        _message_id(message, f"msg-{index}")
        for index, message in enumerate(removable)
    }
    covered_tool_call_ids: set[str] = set()
    for message in removable:
        tool_call_id = getattr(message, "tool_call_id", None)
        if isinstance(tool_call_id, str):
            covered_tool_call_ids.add(tool_call_id)
        for call in getattr(message, "tool_calls", None) or []:
            call_id = call.get("id") if isinstance(call, dict) else None
            if isinstance(call_id, str):
                covered_tool_call_ids.add(call_id)

    covered: list[dict[str, Any]] = []
    for ref in source_refs:
        message_id = ref.get("message_id")
        tool_call_id = ref.get("tool_call_id")
        if message_id in covered_message_ids or tool_call_id in covered_tool_call_ids:
            covered.append(ref)
    return covered


def build_summary_update(
    messages: list[Any],
    *,
    previous_summary: str | None,
    new_summary: str,
    keep_recent: int,
    keep_recent_turns: int | None = None,
    summary_json: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    keep_start = _protected_recent_start(
        messages,
        keep_recent,
        keep_recent_turns=keep_recent_turns,
    )
    removable = messages[:keep_start]
    covered_message_ids = [
        _message_id(message, f"msg-{index}")
        for index, message in enumerate(removable)
    ]
    remove_messages = [
        RemoveMessage(id=message_id)
        for message_id in covered_message_ids
    ]
    covered_refs = _covered_source_refs(source_refs, removable)
    summary_payload = summary_json if isinstance(summary_json, dict) else None
    if summary_payload is None:
        summary_payload = normalize_summary_output(new_summary).get("summary_json")
    coverage = summary_payload.get("coverage") if isinstance(summary_payload, dict) else None
    if isinstance(coverage, dict):
        coverage["covered_message_ids"] = covered_message_ids
        coverage["covered_source_event_ids"] = [
            str(ref.get("event_id"))
            for ref in covered_refs
            if ref.get("event_id")
        ]
        coverage["authoritative_tail_starts_after"] = covered_message_ids[-1] if covered_message_ids else ""
    source_ref_lines = [
        f"source:{ref.get('event_id')} tool={ref.get('tool_name')} call={ref.get('tool_call_id')}"
        for ref in covered_refs
        if ref.get("event_id")
    ]
    summary_text = new_summary
    if source_ref_lines:
        summary_text = (
            new_summary.rstrip()
            + "\n\n<covered_source_refs>\n"
            + "\n".join(source_ref_lines)
            + "\n</covered_source_refs>"
        )
    before_tokens = estimate_messages_tokens(messages)
    after_tokens = estimate_text_tokens(summary_text) + estimate_messages_tokens(messages[keep_start:])
    reduction_ratio = round(max(before_tokens - after_tokens, 0) / before_tokens, 3) if before_tokens else 0.0
    retained_tail = messages[keep_start:]
    tiered_tail = tier_tool_messages(
        retained_tail,
        keep_recent_tool_messages=2,
        max_tool_preview_chars=1000,
    )
    tier_replacements = [
        tiered
        for original, tiered in zip(retained_tail, tiered_tail)
        if tiered is not original
    ]
    return {
        "summary": summary_text,
        "summary_json": summary_payload,
        "messages": [*remove_messages, *tier_replacements],
        "context_budget": {
            "status": "summarized" if remove_messages else "ok",
            "removed_message_count": len(remove_messages),
            "covered_message_ids": covered_message_ids,
            "source_refs": covered_refs,
            "token_before": before_tokens,
            "token_after": after_tokens,
            "last_compaction_reduction_ratio": reduction_ratio,
            "compression_ratio": round(after_tokens / before_tokens, 3) if before_tokens else 0.0,
            "previous_summary_present": bool(previous_summary),
        },
    }


def _summary_memory_content(item: Any) -> tuple[str, float, str]:
    if isinstance(item, dict):
        content = str(item.get("content") or item.get("text") or item.get("value") or "").strip()
        confidence = float(item.get("confidence") or 0.8)
        kind = str(item.get("kind") or "stable_preference")
        return content, confidence, kind
    return str(item or "").strip(), 0.8, "stable_preference"


async def persist_summary_memories(
    store: Any,
    *,
    user_id: str | None,
    summary_json: dict[str, Any],
    min_confidence: float = 0.75,
) -> list[dict[str, Any]]:
    if store is None or not user_id or not isinstance(summary_json, dict):
        return []
    written: list[dict[str, Any]] = []
    stable = summary_json.get("stable_preferences")
    preferences = summary_json.get("user_preferences")
    candidates: list[Any] = []
    if isinstance(stable, list):
        candidates.extend(stable)
    if isinstance(preferences, list):
        candidates.extend(
            item for item in preferences
            if not isinstance(item, dict) or item.get("scope") in (None, "long_term")
        )
    if not candidates:
        return []
    for item in candidates:
        content, confidence, kind = _summary_memory_content(item)
        if not content or confidence < min_confidence:
            continue
        digest = hashlib.sha256(content.lower().encode("utf-8")).hexdigest()[:16]
        memory = await write_user_memory(
            store,
            user_id=user_id,
            content=content,
            kind=kind or "stable_preference",
            confidence=confidence,
            memory_id=f"summary:{digest}",
            metadata={"source": "summary_compaction"},
        )
        written.append(memory)
    return written


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def store_put(store: Any, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
    if store is None:
        return None
    method = getattr(store, "aput", None) or getattr(store, "put", None)
    if method is None:
        return None
    try:
        await _maybe_await(method(namespace, key, value))
    except TypeError:
        await _maybe_await(method(namespace, key, value, index=False))


async def store_search(
    store: Any,
    namespace: tuple[str, ...],
    *,
    query: str | None = None,
    limit: int = 5,
) -> list[Any]:
    if store is None:
        return []
    method = getattr(store, "asearch", None) or getattr(store, "search", None)
    if method is None:
        return []
    return list(await _maybe_await(method(namespace, query=query, limit=limit)))


async def store_get(store: Any, namespace: tuple[str, ...], key: str) -> Any:
    if store is None:
        return None
    method = getattr(store, "aget", None) or getattr(store, "get", None)
    if method is None:
        return None
    return await _maybe_await(method(namespace, key))


async def store_delete(store: Any, namespace: tuple[str, ...], key: str) -> None:
    if store is None:
        return None
    method = getattr(store, "adelete", None) or getattr(store, "delete", None)
    if method is None:
        return None
    await _maybe_await(method(namespace, key))


def _item_key(item: Any) -> str | None:
    value = getattr(item, "key", None)
    return str(value) if value is not None else None


def _item_namespace(item: Any) -> list[str]:
    namespace = getattr(item, "namespace", None)
    if isinstance(namespace, (list, tuple)):
        return [str(part) for part in namespace]
    return []


def _item_value(item: Any) -> dict[str, Any]:
    value = getattr(item, "value", None)
    return value if isinstance(value, dict) else {}


async def load_user_memories(
    store: Any,
    *,
    user_id: str | None,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not user_id:
        return []
    hits = await store_search(store, ("memories", user_id), query=query, limit=limit)
    records = []
    for item in hits:
        value = _item_value(item)
        content = str(value.get("content") or value.get("data") or "").strip()
        if not content:
            continue
        records.append(
            {
                "id": _item_key(item),
                "namespace": _item_namespace(item),
                "content": content,
                "kind": value.get("kind"),
                "confidence": value.get("confidence"),
                "score": getattr(item, "score", None),
            }
        )
    return records


async def write_user_memory(
    store: Any,
    *,
    user_id: str,
    content: str,
    kind: str,
    confidence: float = 0.8,
    memory_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    namespace = ("memories", user_id)
    key = memory_id or str(uuid4())
    value = {
        "content": content,
        "kind": kind,
        "confidence": confidence,
        "metadata": metadata or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await store_put(store, namespace, key, value)
    return {"memory_id": key, "namespace": list(namespace), **value}


async def update_user_memory(store: Any, *, user_id: str, memory_id: str, content: str) -> dict[str, Any]:
    namespace = ("memories", user_id)
    existing = await store_get(store, namespace, memory_id)
    value = _item_value(existing) if existing is not None else {}
    value["content"] = content
    value["updated_at"] = datetime.now(timezone.utc).isoformat()
    await store_put(store, namespace, memory_id, value)
    return {"memory_id": memory_id, "namespace": list(namespace), **value}


async def forget_user_memory(store: Any, *, user_id: str, memory_id: str) -> dict[str, Any]:
    namespace = ("memories", user_id)
    await store_delete(store, namespace, memory_id)
    return {"memory_id": memory_id, "namespace": list(namespace), "deleted": True}


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


async def save_source_event(
    store: Any,
    *,
    thread_id: str,
    tool_name: str,
    tool_call_id: str | None,
    args: dict[str, Any],
    result: Any,
    preview: Any,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    event_id = str(uuid4())
    namespace = ("source_events", thread_id)
    value = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "args": args,
        "result": result,
        "preview": preview,
        "content_preview": _safe_json(preview)[:1000],
        "checkpoint_id": checkpoint_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await store_put(store, namespace, event_id, value)
    return {"event_id": event_id, "namespace": list(namespace), **value}


async def search_source_events(
    store: Any,
    *,
    thread_id: str,
    query: str,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    hits = await store_search(store, ("source_events", thread_id), query=query, limit=top_k)
    records = []
    for item in hits:
        value = _item_value(item)
        records.append(
            {
                "event_id": _item_key(item),
                "tool_name": value.get("tool_name"),
                "content_preview": str(value.get("content_preview") or "")[:1000],
                "score": getattr(item, "score", None),
                "metadata": {
                    "tool_call_id": value.get("tool_call_id"),
                    "checkpoint_id": value.get("checkpoint_id"),
                    "namespace": _item_namespace(item),
                },
            }
        )
    return records


async def save_compaction_run(
    store: Any,
    *,
    thread_id: str,
    summary_update: dict[str, Any],
    status: str = "completed",
    error_type: str | None = None,
) -> None:
    budget = summary_update.get("context_budget") if isinstance(summary_update, dict) else {}
    value = {
        "status": status,
        "error_type": error_type,
        "summary_present": bool(summary_update.get("summary")) if isinstance(summary_update, dict) else False,
        "summary_json": summary_update.get("summary_json") if isinstance(summary_update, dict) else None,
        "budget": budget if isinstance(budget, dict) else {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await store_put(store, ("compaction_runs", thread_id), str(uuid4()), value)

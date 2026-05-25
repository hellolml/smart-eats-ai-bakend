from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence


@dataclass(slots=True)
class ContextRequest:
    thread_id: str
    user_id: str | None
    message: str | None
    scene: str = "chat"
    system_prompt: str = ""
    provider: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    context_overrides: dict[str, Any] | None = None


@dataclass(slots=True)
class ContextEvent:
    id: str
    thread_id: str
    type: str
    role: str | None = None
    content: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    token_estimate: int = 0
    pinned: bool = False
    critical: bool = False
    created_at: datetime | None = None


@dataclass(slots=True)
class ContextBlock:
    kind: str
    content: str
    priority: int = 100
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    token_estimate: int = 0
    safe_to_send: bool = True


@dataclass(slots=True)
class ContextCondensation:
    id: str
    thread_id: str
    summary: str
    summary_json: dict[str, Any]
    covered_event_ids: list[str]
    summary_offset: int
    status: str = "completed"
    model: str | None = None
    prompt_version: str | None = None
    token_before: int = 0
    token_after: int = 0
    created_at: datetime | None = None


@dataclass(slots=True)
class MemoryCandidate:
    kind: str
    content: str
    confidence: float = 0.0
    ttl: str = "none"
    source_event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryRecord:
    id: str
    namespace: tuple[str, ...]
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class SourceEventHit:
    event_id: str
    thread_id: str
    content_preview: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompactionRun:
    id: str
    thread_id: str
    condensation_id: str | None = None
    trigger_reason: str = "budget_hard_limit"
    model: str | None = None
    prompt_version: str | None = None
    input_event_count: int = 0
    input_token_estimate: int = 0
    output_token_estimate: int = 0
    compression_ratio: float = 0.0
    latency_ms: int = 0
    status: str = "completed"
    error_type: str | None = None
    error_message: str | None = None
    quality_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(slots=True)
class BudgetReport:
    total_tokens: int
    max_tokens: int
    buckets: dict[str, int] = field(default_factory=dict)
    dropped_blocks: list[str] = field(default_factory=list)
    dropped_event_ids: list[str] = field(default_factory=list)
    condensation_triggered: bool = False
    status: str = "ok"


@dataclass(slots=True)
class PreparedContext:
    messages: list[Any]
    tools: list[Any] = field(default_factory=list)
    runtime: dict[str, Any] = field(default_factory=dict)
    blocks: list[ContextBlock] = field(default_factory=list)
    memories: list[MemoryRecord] = field(default_factory=list)
    budget_report: BudgetReport | None = None


def namespace_tuple(namespace: Sequence[str] | str | None) -> tuple[str, ...]:
    if namespace is None:
        return tuple()
    if isinstance(namespace, str):
        return (namespace,)
    return tuple(str(item) for item in namespace if str(item))

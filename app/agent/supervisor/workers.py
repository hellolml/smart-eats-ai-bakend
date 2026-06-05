from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agent.agent_state import (
    AgentContext,
    agent_context_from_mapping,
    dump_agent_context,
    empty_agent_context,
    merge_agent_context,
)
from app.agent.intent import infer_food_worker_intent
from app.agent.runtime.graph import (
    AgentRuntimeGraphState,
    build_cached_agent_runtime_graph,
    runtime_graph_configurable,
)
from app.agent.runtime.finalization import fallback_final
from app.domain.preferences.markdown_profile import (
    build_preference_context,
    ensure_user_preference_file,
    update_user_preference_profile,
)


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    scene: str
    agent_id: str | None = None
    plan_type: str | None = None
    intent: str | None = None
    forced_skill_ids: tuple[str, ...] = ()
    description: str = ""


WORKER_SPECS: tuple[WorkerSpec, ...] = (
    WorkerSpec(
        name="travel_planner",
        scene="travel_planner",
        agent_id="travel_plan",
        plan_type="travel",
        forced_skill_ids=("travel_plan_new",),
        description="旅行规划、攻略截图、POI 候选、行程和高德地图。",
    ),
    WorkerSpec(
        name="food_advisor",
        scene="eat",
        agent_id="food_decision",
        description="外出吃饭、附近餐厅、今天吃什么。",
    ),
    WorkerSpec(
        name="route_planner",
        scene="route",
        intent="route",
        forced_skill_ids=("route_planner",),
        description="路线、导航、怎么去某个目的地。",
    ),
    WorkerSpec(
        name="home_chef",
        scene="home_chef",
        intent="cook_home",
        forced_skill_ids=("home_chef",),
        description="在家做饭、冰箱食材、菜谱。",
    ),
    WorkerSpec(
        name="general_chat",
        scene="chat",
        description="普通聊天、记忆读写和不需要业务工具的回答。",
    ),
)


def build_worker_agents(
    *,
    db: Any,
    redis_client: Any,
    provider: str | None,
    resolved_model_config: dict[str, Any] | None,
) -> list[Any]:
    return [
        build_worker_agent(
            spec,
            db=db,
            redis_client=redis_client,
            provider=provider,
            resolved_model_config=resolved_model_config,
        )
        for spec in WORKER_SPECS
    ]


def build_worker_agent(
    spec: WorkerSpec,
    *,
    db: Any,
    redis_client: Any,
    provider: str | None,
    resolved_model_config: dict[str, Any] | None,
) -> Any:
    async def worker_node(state: dict[str, Any], store: Any = None) -> dict[str, Any]:
        payload = await _prepare_worker_payload(spec, state)
        payload["persist_user_message"] = False
        payload["user_message_logged"] = True
        payload["last_user_message"] = payload.get("message")
        graph = build_cached_agent_runtime_graph(
            provider=provider,
            resolved_model_config=resolved_model_config,
        ).compile(store=store)

        latest: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        async for update in graph.astream(
            payload,
            stream_mode="values",
            config={
                "configurable": {
                    "thread_id": f"{payload.get('session_id')}:{spec.name}",
                    **runtime_graph_configurable(db=db, redis_client=redis_client),
                }
            },
        ):
            if isinstance(update, dict):
                latest = update
                update_events = update.get("events")
                if isinstance(update_events, list):
                    events.extend(item for item in update_events if isinstance(item, dict))
                    update["events"] = []

        final_json = _final_json_from_update(latest)
        text = _render_worker_text(final_json)
        return {
            **(latest or {}),
            "messages": [
                AIMessage(
                    content=text,
                    name=spec.name,
                    additional_kwargs={
                        "agent_id": spec.agent_id or spec.name,
                        "plan_type": spec.plan_type,
                    },
                )
            ],
            "events": events,
            "final_json": final_json,
            "agent_id": spec.agent_id or spec.name,
            "plan_type": spec.plan_type,
        }

    graph = StateGraph(AgentRuntimeGraphState)
    graph.add_node(spec.name, worker_node)
    graph.add_edge(START, spec.name)
    graph.add_edge(spec.name, END)
    return graph.compile(name=spec.name)


async def _prepare_worker_payload(spec: WorkerSpec, state: dict[str, Any]) -> dict[str, Any]:
    payload = _base_payload_from_state(state)
    context = merge_agent_context(_agent_context_from_payload(payload), _worker_context(spec, state))
    _sync_context_payload(payload, context)
    payload["scene"] = spec.scene
    if spec.agent_id:
        payload["agent_id"] = spec.agent_id
    if spec.plan_type:
        payload["plan_type"] = spec.plan_type

    if spec.name == "travel_planner":
        return await _prepare_travel_worker_payload(payload, context)

    if spec.name == "food_advisor":
        return await _prepare_food_worker_payload(payload)

    return payload


async def _prepare_food_worker_payload(payload: dict[str, Any]) -> dict[str, Any]:
    next_payload = dict(payload)
    next_payload["scene"] = "eat" if next_payload.get("scene") in (None, "", "chat") else next_payload.get("scene")
    next_payload["agent_id"] = "food_decision"

    user_id = next_payload.get("user_id")
    await update_user_preference_profile(
        user_id,
        user_text=str(next_payload.get("message") or ""),
        source="food_agent_user_message",
    )
    profile = await ensure_user_preference_file(user_id)
    preference_context = build_preference_context(profile)

    context = _agent_context_from_payload(next_payload)
    intent = infer_food_worker_intent(next_payload.get("message"), explicit_intent=context.intent) or "eat_out"
    context.intent = intent
    context.agent_id = "food_decision"
    context.user_preference_md = preference_context
    context.food_profile = preference_context.get("profile") or {}
    context.forced_skill_ids = _merge_forced_skill_ids(
        context.forced_skill_ids,
        ["home_chef"] if intent == "cook_home" else ["food_decision", "restaurant_finder"],
    )
    _sync_context_payload(next_payload, context)
    return next_payload


async def _prepare_travel_worker_payload(
    payload: dict[str, Any],
    context: AgentContext,
) -> dict[str, Any]:
    next_payload = dict(payload)
    next_payload["scene"] = "travel_planner"
    next_payload["agent_id"] = "travel_plan"
    next_payload["plan_type"] = "travel"

    action = next_payload.get("action") or next_payload.get("travel_action")
    profile = await ensure_user_preference_file(next_payload.get("user_id"))
    preference_context = build_preference_context(profile)

    latest = context.latest_travel_final_json
    latest_final_json = latest if isinstance(latest, dict) else None
    plan_payload = next_payload.get("payload")
    travel_payload = next_payload.get("travel_payload")
    merged_payload: dict[str, Any] = {}
    if latest_final_json:
        merged_payload.update(_travel_context_from_final_json(latest_final_json))
    if isinstance(travel_payload, dict):
        merged_payload.update(travel_payload)
    if isinstance(plan_payload, dict):
        merged_payload.update(plan_payload)
    if _has_new_attachments(merged_payload):
        action = "refresh_sources"
    if action:
        next_payload["travel_action"] = action
    if action == "refresh_sources":
        _mark_refresh_sources(merged_payload, latest_final_json)
    if merged_payload:
        next_payload["travel_payload"] = merged_payload
        next_payload["payload"] = merged_payload

    context = _agent_context_from_payload(next_payload)
    context.agent_id = "travel_plan"
    context.plan_type = "travel"
    context.plan_agent = {
        "agent_id": "travel_plan",
        "plan_type": "travel",
        "state": merged_payload.get("state") if merged_payload else None,
    }
    context.user_preference_md = preference_context
    context.travel_food_preferences = preference_context.get("profile") or {}
    context.travel_food_preference_summary = preference_context.get("summary")
    if action:
        context.action = action
    if merged_payload:
        context.payload = merged_payload
        context.travel_payload = merged_payload
    if action == "refresh_sources":
        context.travel_refresh_sources = True
    _sync_context_payload(next_payload, context)
    return next_payload


def _base_payload_from_state(state: dict[str, Any]) -> dict[str, Any]:
    message = state.get("message") or _latest_human_message(state.get("messages"))
    payload = {
        key: value
        for key, value in state.items()
        if key
        in {
            "session_id",
            "user_id",
            "message",
            "trace_id",
            "scene",
            "agent_id",
            "plan_type",
            "context_overrides",
            "snapshot",
            "context",
            "steps_left",
            "provider",
            "resolved_model_config",
            "client_ip",
            "last_user_message",
            "history",
            "summary",
            "context_budget",
            "retrieved_memories",
            "source_refs",
            "action",
            "payload",
            "travel_action",
            "travel_payload",
        }
    }
    payload["message"] = message
    if isinstance(state.get("context_overrides"), dict):
        payload["client_context_overrides"] = dict(state["context_overrides"])
    return payload


def _latest_human_message(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content or "")
    return ""


def _agent_context_from_payload(payload: dict[str, Any]) -> AgentContext:
    return (
        agent_context_from_mapping(payload.get("client_context_overrides"))
        or agent_context_from_mapping(payload.get("context_overrides"))
        or empty_agent_context()
    )


def _sync_context_payload(payload: dict[str, Any], context: AgentContext) -> None:
    context_payload = dump_agent_context(context)
    payload["client_context_overrides"] = context_payload
    payload["context_overrides"] = context_payload


def _merge_forced_skill_ids(existing: Any, required: list[str]) -> list[str]:
    values: list[str] = []
    if isinstance(existing, str):
        values.append(existing)
    elif isinstance(existing, list):
        values.extend(item for item in existing if isinstance(item, str))
    for item in required:
        if item not in values:
            values.append(item)
    return values


def _travel_context_from_final_json(latest: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "previous_final_json",
        "state",
        "trip_meta",
        "sources",
        "places",
        "candidates",
        "failed_places",
        "food_items",
        "candidate_groups",
        "itinerary",
        "map",
        "raw_text",
    )
    payload = {key: latest.get(key) for key in keys if latest.get(key) not in (None, [], {})}
    payload["previous_final_json"] = latest
    return payload


def _has_new_attachments(payload: dict[str, Any]) -> bool:
    value = payload.get("new_attachments")
    return isinstance(value, list) and any(isinstance(item, dict) for item in value)


def _mark_refresh_sources(payload: dict[str, Any], latest: dict[str, Any] | None) -> None:
    previous = latest if isinstance(latest, dict) else payload.get("previous_final_json")
    if isinstance(previous, dict):
        if previous.get("itinerary") and not payload.get("previous_itinerary"):
            payload["previous_itinerary"] = previous.get("itinerary")
        if previous.get("map") and not payload.get("previous_map"):
            payload["previous_map"] = previous.get("map")
    payload["state"] = "ingesting_content"
    payload["refresh_sources"] = True
    payload["stale_artifacts"] = {
        "itinerary": bool(payload.get("previous_itinerary")),
        "map": bool(payload.get("previous_map")),
        "reason": "new_attachments",
    }
    payload.pop("itinerary", None)
    payload.pop("map", None)


def _worker_context(spec: WorkerSpec, state: dict[str, Any]) -> AgentContext:
    overrides: dict[str, Any] = {
        "agent_id": spec.agent_id or spec.name,
        "ui_scene": spec.scene,
    }
    if spec.intent:
        overrides["intent"] = spec.intent
    if spec.plan_type:
        overrides["plan_type"] = spec.plan_type
    if spec.forced_skill_ids:
        overrides["forced_skill_ids"] = list(spec.forced_skill_ids)
    if spec.name == "general_chat":
        overrides["allowed_tools"] = [
            "memory_search",
            "memory_write",
            "memory_update",
            "memory_forget",
            "source_event_search",
        ]
    context_overrides = state.get("context_overrides")
    if isinstance(context_overrides, dict) and isinstance(context_overrides.get("latest_travel_final_json"), dict):
        overrides["latest_travel_final_json"] = context_overrides["latest_travel_final_json"]
    return AgentContext.model_validate(overrides)


def _final_json_from_update(update: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(update, dict) and isinstance(update.get("final_json"), dict):
        return update["final_json"]
    return fallback_final()


def _render_worker_text(final_json: dict[str, Any]) -> str:
    raw_text = final_json.get("raw_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    recs = final_json.get("recommendations")
    if isinstance(recs, list) and recs:
        lines: list[str] = []
        for item in recs:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                reason = str(item.get("reason") or "").strip()
                if title and reason:
                    lines.append(f"{title}（{reason}）")
                elif title:
                    lines.append(title)
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
        if lines:
            return "\n".join(lines)
    return "好的。"

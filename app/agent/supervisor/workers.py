from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agent.multi_agent.base import AgentTurnContext
from app.agent.multi_agent.food import FoodDecisionAgent
from app.agent.multi_agent.travel import TravelPlanAgent
from app.agent.runtime.graph import AgentRuntimeGraphState, build_agent_runtime_graph
from app.agent.runtime.finalization import fallback_final


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
        intent="eat_out",
        forced_skill_ids=("food_decision", "restaurant_finder"),
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
        graph = build_agent_runtime_graph(
            db=db,
            redis_client=redis_client,
            provider=provider,
            resolved_model_config=resolved_model_config,
        ).compile(store=store)

        latest: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        async for update in graph.astream(
            payload,
            stream_mode="values",
            config={"configurable": {"thread_id": f"{payload.get('session_id')}:{spec.name}"}},
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
                        "final_json": final_json,
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
    overrides = _context_overrides(payload)
    overrides = _merge_context(overrides, _worker_overrides(spec, state))
    payload["client_context_overrides"] = overrides
    payload["context_overrides"] = overrides
    payload["scene"] = spec.scene
    if spec.agent_id:
        payload["agent_id"] = spec.agent_id
    if spec.plan_type:
        payload["plan_type"] = spec.plan_type

    if spec.name == "travel_planner":
        latest = overrides.get("latest_travel_final_json")
        prepared = await TravelPlanAgent().prepare_turn(
            AgentTurnContext(
                session_id=str(payload.get("session_id") or ""),
                user_id=payload.get("user_id"),
                payload=payload,
                latest_final_json=latest if isinstance(latest, dict) else None,
            )
        )
        return {**payload, **prepared.payload, "context_overrides": prepared.context_overrides}

    if spec.name == "food_advisor":
        prepared = await FoodDecisionAgent().prepare_turn(
            AgentTurnContext(
                session_id=str(payload.get("session_id") or ""),
                user_id=payload.get("user_id"),
                payload=payload,
                latest_final_json=None,
            )
        )
        return {**payload, **prepared.payload, "context_overrides": prepared.context_overrides}

    return payload


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


def _context_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("client_context_overrides") or payload.get("context_overrides")
    return dict(value) if isinstance(value, dict) else {}


def _worker_overrides(spec: WorkerSpec, state: dict[str, Any]) -> dict[str, Any]:
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
    return overrides


def _merge_context(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_context(merged[key], value)
        else:
            merged[key] = value
    return merged


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

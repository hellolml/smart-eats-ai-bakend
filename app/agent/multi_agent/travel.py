from __future__ import annotations

from typing import Any

from app.agent.multi_agent.base import AgentTurnContext, PreparedAgentTurn
from app.domain.preferences.markdown_profile import build_preference_context, ensure_user_preference_file


class TravelPlanAgent:
    agent_id = "travel_plan"
    plan_type = "travel"
    scenes = {"travel_planner"}

    def matches(self, payload: dict[str, Any]) -> bool:
        agent_id = str(payload.get("agent_id") or "").strip()
        plan_type = str(payload.get("plan_type") or "").strip()
        scene = str(payload.get("scene") or "").strip()
        return (
            agent_id in {self.agent_id, "travel_planner"}
            or plan_type == self.plan_type
            or scene in self.scenes
        )

    async def prepare_turn(self, context: AgentTurnContext) -> PreparedAgentTurn:
        payload = dict(context.payload)
        payload["scene"] = "travel_planner"
        payload["agent_id"] = self.agent_id
        payload["plan_type"] = self.plan_type

        action = payload.get("action") or payload.get("travel_action")
        profile = await ensure_user_preference_file(context.user_id)
        preference_context = build_preference_context(profile)

        plan_payload = payload.get("payload")
        travel_payload = payload.get("travel_payload")
        merged_payload: dict[str, Any] = {}
        if context.latest_final_json:
            merged_payload.update(_context_from_final_json(context.latest_final_json))
        if isinstance(travel_payload, dict):
            merged_payload.update(travel_payload)
        if isinstance(plan_payload, dict):
            merged_payload.update(plan_payload)
        if _has_new_attachments(merged_payload):
            action = "refresh_sources"
        if action:
            payload["travel_action"] = action
        if action == "refresh_sources":
            _mark_refresh_sources(merged_payload, context.latest_final_json)
        if merged_payload:
            payload["travel_payload"] = merged_payload
            payload["payload"] = merged_payload

        context_overrides = _context_overrides(payload)
        context_overrides["agent_id"] = self.agent_id
        context_overrides["plan_type"] = self.plan_type
        context_overrides["plan_agent"] = {
            "agent_id": self.agent_id,
            "plan_type": self.plan_type,
            "state": merged_payload.get("state") if merged_payload else None,
        }
        context_overrides["user_preference_md"] = preference_context
        context_overrides["travel_food_preferences"] = preference_context.get("profile") or {}
        context_overrides["travel_food_preference_summary"] = preference_context.get("summary")
        if action:
            context_overrides["action"] = action
        if merged_payload:
            context_overrides["payload"] = merged_payload
            context_overrides["travel_payload"] = merged_payload
        if action == "refresh_sources":
            context_overrides["travel_refresh_sources"] = True
        payload["client_context_overrides"] = context_overrides

        return PreparedAgentTurn(
            payload=payload,
            agent_id=self.agent_id,
            plan_type=self.plan_type,
            context_overrides=context_overrides,
        )


def _context_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("client_context_overrides")
    return dict(value) if isinstance(value, dict) else {}


def _context_from_final_json(latest: dict[str, Any]) -> dict[str, Any]:
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

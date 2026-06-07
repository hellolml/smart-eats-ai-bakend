from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TravelWorkflowState(BaseModel):
    model_config = ConfigDict(extra="allow")

    state: str | None = None
    previous_final_json: dict[str, Any] | None = None
    trip_meta: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    places: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    failed_places: list[dict[str, Any]] = Field(default_factory=list)
    food_items: list[dict[str, Any]] = Field(default_factory=list)
    candidate_groups: dict[str, Any] | None = None
    itinerary: dict[str, Any] | None = None
    map: dict[str, Any] | None = None
    raw_text: str | None = None
    refresh_sources: bool = False
    stale_artifacts: dict[str, Any] | None = None

    @classmethod
    def from_final_json(cls, latest: dict[str, Any] | None) -> "TravelWorkflowState":
        if not isinstance(latest, dict):
            return cls()
        payload = {
            key: latest.get(key)
            for key in (
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
            if latest.get(key) not in (None, [], {})
        }
        payload["previous_final_json"] = latest
        return cls.model_validate(payload)

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude_defaults=True)


class TravelSourceIngestionService:
    @staticmethod
    def has_new_attachments(payload: dict[str, Any]) -> bool:
        value = payload.get("new_attachments")
        return isinstance(value, list) and any(isinstance(item, dict) for item in value)

    @staticmethod
    def mark_refresh_sources(payload: dict[str, Any], latest: dict[str, Any] | None) -> dict[str, Any]:
        next_payload = dict(payload)
        previous = latest if isinstance(latest, dict) else next_payload.get("previous_final_json")
        if isinstance(previous, dict):
            if previous.get("itinerary") and not next_payload.get("previous_itinerary"):
                next_payload["previous_itinerary"] = previous.get("itinerary")
            if previous.get("map") and not next_payload.get("previous_map"):
                next_payload["previous_map"] = previous.get("map")
        next_payload["state"] = "ingesting_content"
        next_payload["refresh_sources"] = True
        next_payload["stale_artifacts"] = {
            "itinerary": bool(next_payload.get("previous_itinerary")),
            "map": bool(next_payload.get("previous_map")),
            "reason": "new_attachments",
        }
        next_payload.pop("itinerary", None)
        next_payload.pop("map", None)
        return next_payload


class TravelCandidateService:
    @staticmethod
    def context_from_final_json(latest: dict[str, Any] | None) -> dict[str, Any]:
        return TravelWorkflowState.from_final_json(latest).as_payload()

    @staticmethod
    def infer_action_from_message(
        message: str | None,
        *,
        latest: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str | None:
        text = str(message or "").strip()
        if not text:
            return None
        latest = latest if isinstance(latest, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        state = str(payload.get("state") or latest.get("state") or "")
        has_candidates = bool(payload.get("candidates") or latest.get("candidates"))
        has_itinerary = isinstance(payload.get("itinerary") or latest.get("itinerary"), dict)

        if any(token in text for token in ("生成地图", "生成二维码", "高德地图", "地图二维码")):
            return "generate_map" if has_itinerary or state == "itinerary_generated" else None

        confirm_text = any(token in text for token in ("确认", "可以", "继续", "没问题", "就这样", "下一步"))
        wants_itinerary = any(token in text for token in ("生成行程", "最终每日行程", "每日行程", "继续生成"))
        if (state == "candidates_ready" or has_candidates) and (confirm_text or wants_itinerary):
            return "confirm_candidates"
        if (state == "itinerary_generated" or has_itinerary) and confirm_text:
            return "generate_map"
        return None


class TravelItineraryService:
    @staticmethod
    def has_itinerary(payload: dict[str, Any]) -> bool:
        return isinstance(payload.get("itinerary"), dict) or isinstance(payload.get("previous_itinerary"), dict)


class TravelMapService:
    @staticmethod
    def has_map(payload: dict[str, Any]) -> bool:
        return isinstance(payload.get("map"), dict) or isinstance(payload.get("previous_map"), dict)

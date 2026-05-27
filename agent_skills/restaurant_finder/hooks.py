from __future__ import annotations

from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


class RestaurantFinderHooks(BaseSkillHooks):
    async def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        redis_client = runtime.get("redis_client") if isinstance(runtime, dict) else None
        extra: dict[str, Any] = {}
        if redis_client is None:
            return extra

        from app.agent.tools.location_cache import load_cached_location
        from app.agent.tools.restaurant_cache import load_cached_restaurants

        cached_location = await load_cached_location(redis_client, getattr(state, "session_id", ""))
        if isinstance(cached_location, dict) and cached_location.get("lat") is not None and cached_location.get("lng") is not None:
            extra["cached_location"] = {
                "lat": cached_location.get("lat"),
                "lng": cached_location.get("lng"),
                "city": cached_location.get("city"),
            }
            extra.setdefault(
                "location",
                {"lat": cached_location.get("lat"), "lng": cached_location.get("lng")},
            )
            if isinstance(cached_location.get("city"), str) and cached_location.get("city").strip():
                extra.setdefault("city", cached_location.get("city"))

        cached_items = await load_cached_restaurants(redis_client, getattr(state, "session_id", ""))
        if isinstance(cached_items, list):
            cleaned = [item for item in cached_items if isinstance(item, dict)]
            if cleaned:
                extra["last_restaurants"] = cleaned
        return extra

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "geocode_location":
            updated = dict(args)
            if "query" not in updated and "location" in updated:
                updated["query"] = updated.pop("location")
            return updated
        if tool_name != "search_restaurants":
            return args
        updated = dict(args)
        if "query" not in updated and isinstance(updated.get("keyword"), str):
            updated["query"] = updated.pop("keyword")
        location = updated.get("location")
        if isinstance(location, dict):
            if "lat" not in updated:
                updated["lat"] = location.get("lat")
            if "lng" not in updated:
                updated["lng"] = location.get("lng")
            updated.pop("location", None)
        updated.pop("radius", None)
        for key in ("lat", "lng"):
            value = updated.get(key)
            if isinstance(value, (int, float)) and float(value) == 0.0:
                updated.pop(key, None)
        query = updated.get("query")
        if isinstance(query, str) and not query.strip():
            updated.pop("query", None)
        return updated

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if tool_name in {"get_ip_location", "geocode_location"}:
            return self._handle_location(state, tool_name, result)
        if tool_name == "search_restaurants":
            return self._handle_search_restaurants(state, result)
        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        last_error: str | None = None
        for item in reversed(getattr(state, "observations", []) or []):
            if not isinstance(item, dict):
                continue
            tool = item.get("tool")
            result = item.get("result")
            if tool == "search_restaurants" and isinstance(result, list) and result:
                names = [
                    str(row.get("name") or "").strip()
                    for row in result[:3]
                    if isinstance(row, dict) and str(row.get("name") or "").strip()
                ]
                if names:
                    return _note_final(
                        "我先给你整理了附近可选店",
                        "基于已拿到的检索结果",
                        [f"你可以先看这几家：{'；'.join(names)}", "要不要我再按口味帮你筛一轮？"],
                    )
            if isinstance(result, dict) and isinstance(result.get("error"), str):
                last_error = result.get("error")
        if last_error in {"missing_location", "missing_ip"}:
            return _note_final(
                "我还缺少精确位置，暂时没法推荐附近餐厅。",
                "位置信息不足",
                ["你可以发我当前城市或地标", "或者改为在家做饭也可以。"],
            )
        return None

    def _handle_location(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        context = _ensure_context(state)
        if result.get("error"):
            context["last_location_error"] = result.get("error")
            _observe_recovery(state, tool_name, result)
            return None
        context["location"] = {"lat": result.get("lat"), "lng": result.get("lng")}
        if result.get("city"):
            context["city"] = result.get("city")
        source = result.get("location_source") or context.get("location_source")
        if isinstance(source, str) and source:
            context["location_source"] = source
        context["task_stage"] = "location_ready"
        return None

    def _handle_search_restaurants(self, state: Any, result: Any) -> dict[str, Any] | None:
        context = _ensure_context(state)
        context["task_stage"] = "searched"
        if isinstance(result, dict) and result.get("error"):
            context["last_search_error"] = result.get("error")
            context.pop("suggested_radius_km", None)
            _clear_system_directive(state)
            _observe_recovery(state, "search_restaurants", result)
            return None
        if isinstance(result, list) and not result:
            retries = int(context.get("restaurant_retries") or 0) + 1
            context["restaurant_retries"] = retries
            context["last_search_error"] = "empty_result"
            context.pop("suggested_radius_km", None)
            overrides = _ensure_context_overrides(state)
            overrides["restaurant_search_retries"] = retries
            overrides.pop("system_directive", None)
            _prune_empty_context_overrides(state)
            return None
        if isinstance(result, list) and result:
            context.pop("last_search_error", None)
            context.pop("restaurant_retries", None)
            context.pop("suggested_radius_km", None)
            if isinstance(getattr(state, "context_overrides", None), dict):
                state.context_overrides.pop("restaurant_search_retries", None)
                state.context_overrides.pop("system_directive", None)
                _prune_empty_context_overrides(state)
        return None


def _ensure_context(state: Any) -> dict[str, Any]:
    if getattr(state, "context", None) is None:
        state.context = {}
    return state.context


def _ensure_context_overrides(state: Any) -> dict[str, Any]:
    if getattr(state, "context_overrides", None) is None:
        state.context_overrides = {}
    return state.context_overrides


def _prune_empty_context_overrides(state: Any) -> None:
    if isinstance(getattr(state, "context_overrides", None), dict) and not state.context_overrides:
        state.context_overrides = None


def _clear_system_directive(state: Any) -> None:
    if isinstance(getattr(state, "context_overrides", None), dict):
        state.context_overrides.pop("system_directive", None)
        _prune_empty_context_overrides(state)


def _observe_recovery(state: Any, tool_name: str, result: dict[str, Any]) -> None:
    error = result.get("error")
    if not error:
        return
    context = _ensure_context(state)
    path = context.setdefault("recovery_path", [])
    if not isinstance(path, list):
        return
    step = f"{tool_name}:{error}"
    if step not in path:
        path.append(step)


def _note_final(title: str, reason: str, followups: list[str]) -> dict[str, Any]:
    return {
        "recommendations": [{"type": "note", "title": title, "reason": reason}],
        "followups": followups,
        "warnings": [],
    }

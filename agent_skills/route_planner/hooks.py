from __future__ import annotations

import re
from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


PLAN_ROUTE_PREVIEW_FIELDS = (
    "distance_m",
    "duration_s",
    "steps",
    "segments",
    "origin",
    "destination",
    "mode",
    "fallback_from",
    "error",
)


class RoutePlannerHooks(BaseSkillHooks):
    async def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidates = context.get("last_restaurants")
        if not isinstance(candidates, list):
            redis_client = runtime.get("redis_client") if isinstance(runtime, dict) else None
            if redis_client is not None:
                from app.agent.tools.restaurant_cache import load_cached_restaurants

                cached = await load_cached_restaurants(redis_client, getattr(state, "session_id", ""))
                candidates = cached if isinstance(cached, list) else []
        cleaned = [item for item in candidates or [] if isinstance(item, dict)]
        target = _extract_target_from_candidates(getattr(state, "message", None), cleaned)
        extra: dict[str, Any] = {}
        if cleaned and "last_restaurants" not in context:
            extra["last_restaurants"] = cleaned
        if isinstance(target, dict):
            extra["route_target_candidate"] = target
        return extra

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "geocode_location":
            return args
        updated = dict(args)
        if "query" not in updated and "location" in updated:
            updated["query"] = updated.pop("location")
        return updated

    def preview_tool_result(self, state: Any, tool_name: str, result: Any) -> Any | None:
        if tool_name == "plan_route" and isinstance(result, dict):
            return {field: result.get(field) for field in PLAN_ROUTE_PREVIEW_FIELDS}
        return None

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if tool_name != "plan_route" or not isinstance(result, dict):
            return None
        error = result.get("error")
        if not error:
            if any(result.get(field) for field in ("distance_m", "duration_s", "origin", "destination", "steps", "segments")):
                overrides = _ensure_context_overrides(state)
                overrides["latest_route"] = {
                    field: result.get(field)
                    for field in PLAN_ROUTE_PREVIEW_FIELDS
                    if result.get(field) is not None
                }
                overrides["system_directive"] = (
                    "你已经拿到路线规划结果。请不要再调用其他工具，立即调用 submit_final_answer。"
                    "请严格基于 context.latest_route 与最新的 plan_route 观察结果给出最终回复："
                    "先给路线结论，再给关键步骤（例如距离、预计时长、分步指引）；"
                    "若存在 steps/segments，优先提炼其中关键信息。"
                )
            return None
        if error == "missing_origin":
            return _note_final(
                "还需要你的出发位置，才能规划路线。",
                "系统判定缺少起点信息。",
                ["你现在在哪个城市或位置？", "告诉我你的出发地/地标？"],
            )
        if error == "missing_destination":
            return _note_final(
                "还需要你的目的地，才能规划路线。",
                "终点信息缺失。",
                ["想去哪儿？给我目的地名称。"],
            )
        return _note_final("路线规划失败", "暂时无法获取路线信息。", ["换个出发地或目的地试试？"])


def _ensure_context_overrides(state: Any) -> dict[str, Any]:
    if getattr(state, "context_overrides", None) is None:
        state.context_overrides = {}
    return state.context_overrides


def _note_final(title: str, reason: str, followups: list[str]) -> dict[str, Any]:
    return {
        "recommendations": [{"type": "note", "title": title, "reason": reason}],
        "followups": followups,
        "warnings": [],
    }


CONFIRM_CUES: tuple[str, ...] = (
    "就去",
    "去",
    "选",
    "就这家",
    "这家",
    "那家",
    "安排",
    "走起",
    "前往",
    "带我去",
    "导航",
    "路线",
    "怎么走",
)

INFO_QUERY_CUES: tuple[str, ...] = (
    "怎么样",
    "好吃吗",
    "评价",
    "电话",
    "营业",
    "地址",
    "菜单",
    "人均",
)

NAME_SUFFIXES: tuple[str, ...] = (
    "火锅店",
    "烧烤店",
    "餐厅",
    "饭店",
    "酒店",
    "酒家",
    "小馆",
    "馆",
    "店",
)


def _normalize_match_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s，。！？、,.!?:：；;（）()\[\]{}<>\-_'\"“”‘’]", "", text)


def _coerce_geo_candidate(payload: Any) -> dict[str, float] | None:
    if not isinstance(payload, dict):
        return None
    try:
        return {"lat": float(payload.get("lat")), "lng": float(payload.get("lng"))}
    except (TypeError, ValueError):
        return None


def _extract_target_from_candidates(
    user_message: str | None,
    candidates: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    normalized_message = _normalize_match_text(user_message)
    if not normalized_message or not candidates:
        return None

    raw_message = user_message or ""
    if any(token in raw_message for token in INFO_QUERY_CUES):
        return None

    has_confirm_cue = any(token in raw_message for token in CONFIRM_CUES)
    if not has_confirm_cue and not any(token in raw_message for token in ("导航", "路线", "怎么走", "过去", "到")):
        return None

    best_match: dict[str, Any] | None = None
    best_score = -1
    for row in candidates:
        name = str(row.get("name") or row.get("title") or "").strip()
        if not name:
            continue
        geo = _coerce_geo_candidate(row.get("geo"))
        if not geo:
            continue
        normalized_name = _normalize_match_text(name)
        aliases = {normalized_name}
        for suffix in NAME_SUFFIXES:
            normalized_suffix = _normalize_match_text(suffix)
            if (
                normalized_suffix
                and normalized_name.endswith(normalized_suffix)
                and len(normalized_name) > len(normalized_suffix) + 1
            ):
                aliases.add(normalized_name[: -len(normalized_suffix)])
        for alias in aliases:
            if alias and (alias in normalized_message or normalized_message in alias):
                score = len(alias)
                if score > best_score:
                    best_score = score
                    best_match = {"name": name, "geo": geo}
                break
    return best_match

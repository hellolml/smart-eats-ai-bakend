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
        cached_location: dict[str, Any] | None = None
        redis_client = runtime.get("redis_client") if isinstance(runtime, dict) else None
        if redis_client is not None:
            from app.agent.tools.location_cache import load_cached_location

            cached_location = await load_cached_location(redis_client, getattr(state, "session_id", ""))
        selected = context.get("selected_restaurant")
        cleaned = [item for item in candidates or [] if isinstance(item, dict)]
        if isinstance(selected, dict) and selected not in cleaned:
            cleaned.insert(0, selected)
        target = _selected_target_from_context(getattr(state, "message", None), selected)
        if not target:
            target = _target_from_ordinal(getattr(state, "message", None), cleaned)
        if not target:
            target = _extract_target_from_candidates(getattr(state, "message", None), cleaned)
        extra: dict[str, Any] = {}
        if cleaned and "last_restaurants" not in context:
            extra["last_restaurants"] = cleaned
        if cached_location and "cached_location" not in context:
            extra["cached_location"] = cached_location
        origin = _extract_origin(context, cached_location)
        if origin:
            extra["route_origin"] = origin
        if isinstance(target, dict):
            extra["route_target_candidate"] = target
        return extra

    def short_circuit_final(self, state: Any) -> dict[str, Any] | None:
        context = getattr(state, "context", None)
        context = context if isinstance(context, dict) else {}
        if context.get("route_target_candidate") or _explicit_route_destination(getattr(state, "message", None)):
            return None
        if context.get("last_restaurants"):
            if _references_route(getattr(state, "message", None)):
                return _note_final(
                    "需要先确认具体餐厅。",
                    "你提到了路线，但没有明确选择哪一家餐厅。",
                    ["请告诉我第一家、第二家，或直接说餐厅名称，我再帮你规划路线。"],
                    status="needs_clarification",
                )
            return None
        if not _is_bare_route_followup(getattr(state, "message", None)):
            return None
        return _note_final(
            "你想去哪儿？",
            "当前路线追问缺少明确目的地。",
            ["告诉我餐厅、景点或地址名称，我再帮你规划路线。"],
            status="needs_clarification",
        )

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "plan_route":
            updated = dict(args)
            context = getattr(state, "context", None)
            context = context if isinstance(context, dict) else {}
            origin = _extract_origin(context, context.get("cached_location"))
            target = context.get("route_target_candidate")
            target_geo = _extract_target_geo(target)
            if origin:
                updated.setdefault("origin_lat", origin.get("lat"))
                updated.setdefault("origin_lng", origin.get("lng"))
            if target_geo:
                updated.setdefault("destination_lat", target_geo.get("lat"))
                updated.setdefault("destination_lng", target_geo.get("lng"))
                if isinstance(target, dict) and target.get("name"):
                    updated.setdefault("destination", target.get("name"))
            updated.setdefault("mode", "walking")
            return updated
        if tool_name != "geocode_location":
            return args
        updated = dict(args)
        if "query" not in updated and "location" in updated:
            updated["query"] = updated.pop("location")
        if "query" not in updated:
            origin_query = _explicit_route_origin(getattr(state, "message", None))
            if origin_query:
                updated["query"] = origin_query
        return updated

    def preview_tool_result(self, state: Any, tool_name: str, result: Any) -> Any | None:
        if tool_name == "plan_route" and isinstance(result, dict):
            return {field: result.get(field) for field in PLAN_ROUTE_PREVIEW_FIELDS}
        return None

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if tool_name == "geocode_location" and isinstance(result, dict):
            geo = _coerce_geo_candidate(result) or _extract_origin({"location": result})
            context = getattr(state, "context", None)
            context = context if isinstance(context, dict) else {}
            origin_query = _explicit_route_origin(getattr(state, "message", None))
            result_query = str(result.get("query") or "").strip()
            if geo and origin_query and _query_matches(result_query, origin_query):
                context = getattr(state, "context", None)
                if isinstance(context, dict):
                    context["route_origin"] = geo
                overrides = _ensure_context_overrides(state)
                overrides["route_origin"] = geo
                return None
            target = context.get("route_target_candidate")
            if geo and isinstance(target, dict) and not _extract_target_geo(target):
                target["geo"] = geo
                context["route_target_candidate"] = target
                overrides = _ensure_context_overrides(state)
                overrides["route_target_candidate"] = target
                return None
            if geo and origin_query:
                context = getattr(state, "context", None)
                if isinstance(context, dict):
                    context["route_origin"] = geo
                overrides = _ensure_context_overrides(state)
                overrides["route_origin"] = geo
            return None
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
            return _route_final_from_result(state, result)
        # ── eval: emit recovery SSE event ──
        _emit_recovery_event(state, error, "plan_route")
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

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]] | None:
        if _has_called_tool(state, "plan_route"):
            return None
        context = getattr(state, "context", None)
        context = context if isinstance(context, dict) else {}
        target = context.get("route_target_candidate")
        origin = _extract_origin(context, context.get("cached_location"))
        target_geo = _extract_target_geo(target)
        target_name = str(target.get("name") or "").strip() if isinstance(target, dict) else ""
        if not target_geo and target_name:
            target_geo = _geocoded_geo_for_query(state, target_name)
            if target_geo and isinstance(target, dict):
                target["geo"] = target_geo
                context["route_target_candidate"] = target
        origin_query = _explicit_route_origin(getattr(state, "message", None))
        if not origin and origin_query:
            origin = _geocoded_geo_for_query(state, origin_query)
            if origin:
                context["route_origin"] = origin
        if isinstance(target, dict) and not target_geo:
            if target_name and not _has_geocoded_query(state, target_name):
                return [
                    {
                        "name": "geocode_location",
                        "args": {"query": target_name},
                        "type": "tool_call",
                    }
                ]
        if not origin and target_geo:
            if origin_query and not _has_geocoded_query(state, origin_query):
                return [
                    {
                        "name": "geocode_location",
                        "args": {"query": origin_query},
                        "type": "tool_call",
                    }
                ]
        if not origin or not target_geo:
            return None
        return [
            {
                "name": "plan_route",
                "args": {
                    "origin_lat": origin.get("lat"),
                    "origin_lng": origin.get("lng"),
                    "destination_lat": target_geo.get("lat"),
                    "destination_lng": target_geo.get("lng"),
                    "destination": target.get("name") if isinstance(target, dict) else None,
                    "mode": "walking",
                },
                "type": "tool_call",
            }
        ]

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str] | None:
        route_tools = {"geocode_location", "plan_route"}
        filtered = [tool for tool in allowed_tools if tool in route_tools]
        return filtered if filtered else None


def _has_called_tool(state: Any, tool_name: str) -> bool:
    for attr in ("tool_calls", "observations"):
        values = getattr(state, attr, None)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and item.get("tool") == tool_name:
                return True
            if isinstance(item, dict) and item.get("name") == tool_name:
                return True
    return False


def _has_geocoded_query(state: Any, query: str) -> bool:
    target = _normalize_match_text(query)
    if not target:
        return False
    for attr in ("tool_calls", "observations"):
        values = getattr(state, attr, None)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = item.get("tool") or item.get("name")
            if name != "geocode_location":
                continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            seen = _normalize_match_text(args.get("query") or result.get("query") or "")
            if seen and (target in seen or seen in target):
                return True
    return False


def _geocoded_geo_for_query(state: Any, query: str) -> dict[str, float] | None:
    target = _normalize_match_text(query)
    if not target:
        return None
    for attr in ("observations", "tool_calls"):
        values = getattr(state, attr, None)
        if not isinstance(values, list):
            continue
        for item in reversed(values):
            if not isinstance(item, dict):
                continue
            name = item.get("tool") or item.get("name")
            if name != "geocode_location":
                continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            seen = _normalize_match_text(args.get("query") or result.get("query") or "")
            if not seen or not (target in seen or seen in target):
                continue
            geo = _coerce_geo_candidate(result) or _extract_origin({"location": result})
            if geo:
                return geo
    return None


def _query_matches(value: Any, query: str) -> bool:
    value_norm = _normalize_match_text(value)
    query_norm = _normalize_match_text(query)
    return bool(value_norm and query_norm and (query_norm in value_norm or value_norm in query_norm))


def _ensure_context_overrides(state: Any) -> dict[str, Any]:
    if getattr(state, "context_overrides", None) is None:
        state.context_overrides = {}
    return state.context_overrides


def _note_final(title: str, reason: str, followups: list[str], *, status: str | None = None) -> dict[str, Any]:
    payload = {
        "recommendations": [{"type": "note", "title": title, "reason": reason}],
        "followups": followups,
        "warnings": [],
    }
    if status:
        payload["status"] = status
    return payload


def _route_final_from_result(state: Any, result: dict[str, Any]) -> dict[str, Any]:
    context = getattr(state, "context", None)
    context = context if isinstance(context, dict) else {}
    target = context.get("route_target_candidate")
    target_name = str(target.get("name") or "目的地").strip() if isinstance(target, dict) else "目的地"
    origin_name = _explicit_route_origin(getattr(state, "message", None)) or "出发地"
    mode = _mode_label(result.get("mode") or "walking")
    distance = _format_distance(result.get("distance_m"))
    duration = _format_duration(result.get("duration_s"))
    summary_bits = [f"从{origin_name}到{target_name}的{mode}路线"]
    if distance:
        summary_bits.append(f"距离约{distance}")
    if duration:
        summary_bits.append(f"预计{duration}")
    title = "，".join(summary_bits) + "。"
    steps = _route_step_texts(result)
    followups = steps[:4] if steps else [f"建议按{mode}导航前往，出发前再确认实时路况。"]
    return {
        "recommendations": [{"type": "route", "title": title, "reason": "plan_route"}],
        "followups": followups,
        "warnings": [],
        "state": "route_planned",
    }


def _mode_label(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"walk", "walking"}:
        return "步行"
    if text in {"bike", "bicycling", "cycling"}:
        return "骑行"
    if text in {"drive", "driving"}:
        return "驾车"
    if text in {"transit", "bus", "public"}:
        return "公交/地铁"
    return "路线"


def _format_distance(value: Any) -> str | None:
    try:
        meters = float(value)
    except (TypeError, ValueError):
        return None
    if meters >= 1000:
        return f"{meters / 1000:.1f} 公里"
    return f"{int(round(meters))} 米"


def _format_duration(value: Any) -> str | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    minutes = max(1, int(round(seconds / 60)))
    return f"{minutes} 分钟"


def _route_step_texts(result: dict[str, Any]) -> list[str]:
    steps = result.get("steps")
    if isinstance(steps, list):
        texts = []
        for item in steps:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
            elif isinstance(item, dict):
                text = str(item.get("instruction") or item.get("road") or item.get("action") or "").strip()
                if text:
                    texts.append(text)
        if texts:
            return texts
    segments = result.get("segments")
    if isinstance(segments, list):
        texts = []
        for item in segments:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
            elif isinstance(item, dict):
                text = str(item.get("instruction") or item.get("description") or item.get("name") or "").strip()
                if text:
                    texts.append(text)
        return texts
    return []


def _emit_recovery_event(state: Any, trigger: str, tool_name: str) -> None:
    """Emit a recovery SSE event for evaluation trace collection."""
    events = getattr(state, "events", None)
    if isinstance(events, list):
        events.append(
            {
                "event": "recovery",
                "data": {
                    "path": "clarify" if trigger in ("missing_origin", "missing_destination") else "error_handling",
                    "trigger": trigger,
                    "tool_name": tool_name,
                    "message": f"Route planning encountered: {trigger}",
                },
            }
        )


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

ROUTE_ONLY_CUES: tuple[str, ...] = (
    "怎么走",
    "怎么去",
    "导航",
    "路线",
    "带我去",
    "前往",
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


def _is_bare_route_followup(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = _normalize_match_text(text)
    if not normalized:
        return False
    cue_norms = [_normalize_match_text(cue) for cue in ROUTE_ONLY_CUES]
    filler_norms = {"那", "呢", "啊", "呀", "吗", "一下", "吧", "给我", "帮我", "帮忙"}
    stripped = normalized
    for cue in cue_norms:
        stripped = stripped.replace(cue, "")
    for filler in filler_norms:
        stripped = stripped.replace(filler, "")
    return not stripped and any(cue in normalized for cue in cue_norms)


def _explicit_route_destination(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or _is_bare_route_followup(text):
        return None
    patterns = (
        r"从.+?怎么去(.+)$",
        r"从.+?到(.+)$",
        r"去(.+?)怎么走",
        r"^(?:去|我要去|想去)(.+)$",
        r"怎么去(.+)$",
        r"带我去(.+)$",
        r"前往(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        destination = match.group(1).strip(" ，。！？!?,. ")
        if any(token in destination for token in ("从", "这家", "那家", "第一家", "第二家", "第三家", "更适合")):
            return None
        if destination:
            return destination
    return None


def _explicit_route_origin(value: Any) -> str | None:
    text = str(value or "").strip()
    past_match = re.search(r"从(.+?)过去(?:怎么走|怎么去|路线|导航|$)", text)
    if past_match:
        origin = past_match.group(1).strip(" ，。！？!?,. ")
        origin = re.sub(r"(?:步行|走路|骑行|开车|打车|坐地铁)$", "", origin).strip()
        if origin:
            return origin
    match = re.search(r"从(.+?)(?:到|去).+?(?:怎么走|怎么去|路线|导航|$)", text)
    if not match:
        return None
    origin = match.group(1).strip(" ，。！？!?,. ")
    return origin or None


def _coerce_geo_candidate(payload: Any) -> dict[str, float] | None:
    if not isinstance(payload, dict):
        return None
    try:
        return {"lat": float(payload.get("lat")), "lng": float(payload.get("lng"))}
    except (TypeError, ValueError):
        return None


def _extract_origin(context: dict[str, Any], cached_location: Any = None) -> dict[str, float] | None:
    for source in (
        context.get("location"),
        context.get("route_origin"),
        context.get("cached_location"),
        cached_location,
    ):
        if not isinstance(source, dict):
            continue
        lat = source.get("lat") if source.get("lat") is not None else source.get("latitude")
        lng = source.get("lng") if source.get("lng") is not None else source.get("longitude")
        try:
            return {"lat": float(lat), "lng": float(lng)}
        except (TypeError, ValueError):
            continue
    environment = context.get("environment")
    if isinstance(environment, dict):
        return _extract_origin({"location": environment.get("location")})
    return None


def _extract_target_geo(target: Any) -> dict[str, float] | None:
    if not isinstance(target, dict):
        return None
    geo = _coerce_geo_candidate(target.get("geo"))
    if geo:
        return geo
    raw = target.get("raw") if isinstance(target.get("raw"), dict) else {}
    raw_inner = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    for source in (raw, raw_inner):
        location = source.get("location") if isinstance(source.get("location"), dict) else None
        geo = _coerce_geo_candidate(location) or _extract_origin({"location": location})
        if geo:
            return geo
    return _extract_origin({"location": target})


def _selected_target_from_context(user_message: str | None, selected: Any) -> dict[str, Any] | None:
    if not isinstance(selected, dict):
        return None
    if not (_references_selected_target(user_message) or _is_bare_route_followup(user_message)):
        return None
    geo = _extract_target_geo(selected)
    name = str(selected.get("name") or selected.get("title") or selected.get("verified_name") or "这家餐厅").strip()
    target = {"name": name, "raw": selected}
    if geo:
        target["geo"] = geo
    return target


def _target_from_ordinal(user_message: str | None, candidates: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not candidates or not _references_route(user_message):
        return None
    index = _selection_index(user_message)
    if index is None or index < 0 or index >= len(candidates):
        return None
    candidate = candidates[index]
    geo = _extract_target_geo(candidate)
    name = str(candidate.get("name") or candidate.get("title") or f"第{index + 1}家").strip()
    target = {"name": name, "raw": candidate}
    if geo:
        target["geo"] = geo
    return target


def _references_selected_target(value: Any) -> bool:
    text = str(value or "")
    if not any(token in text for token in ("这家", "那家", "刚才那家", "刚才选的", "选中的", "这家餐厅", "那家餐厅")):
        return False
    return any(token in text for token in ROUTE_ONLY_CUES + ("过去", "到", "走"))


def _references_route(value: Any) -> bool:
    text = str(value or "")
    return any(token in text for token in ROUTE_ONLY_CUES + ("过去", "到", "走"))


def _selection_index(value: Any) -> int | None:
    text = str(value or "")
    digit = re.search(r"第\s*(\d+)\s*家|(\d+)\s*号|第\s*(\d+)\s*个", text)
    if digit:
        for group in digit.groups():
            if group:
                return max(0, int(group) - 1)
    chinese = re.search(r"第?\s*([一二两三四五六七八九十])\s*(?:家|个|号)", text)
    if not chinese and any(token in text for token in ("第一家", "第一个", "第一")):
        return 0
    if chinese:
        index = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.get(chinese.group(1))
        return max(0, index - 1) if index else None
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
        geo = _coerce_geo_candidate(row.get("geo")) or _coerce_geo_candidate(row)
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

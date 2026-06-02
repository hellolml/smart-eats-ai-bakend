from __future__ import annotations

import json
import re
from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


class TravelPlanNewHooks(BaseSkillHooks):
    def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        candidates = _collect_verified_candidates(state)
        map_payload = _latest_map_payload(state)
        itinerary = _payload_itinerary(overrides)
        trip_meta = _trip_meta(state, context, overrides)
        excluded_places = _payload_excluded_places(overrides)
        user_added_places = _payload_user_added_places(overrides)
        extracted_places = _collect_extracted_places(state)
        food_items = _collect_food_items(state)
        phase = _phase(action=action, candidates=candidates, itinerary=itinerary, map_payload=map_payload, trip_meta=trip_meta)

        return {
            "intent": "travel",
            "travel_state": {
                "skill_id": "travel_plan_new",
                "phase": phase,
                "await_confirmation": phase in {"candidates_ready", "itinerary_generated"},
                "travel_action": action,
                "trip_meta": trip_meta,
                "candidates": candidates,
                "extracted_places": extracted_places,
                "excluded_places": excluded_places,
                "user_added_places": user_added_places,
                "food_items": food_items,
                "failed_places": _collect_failed_places(state),
                "itinerary": itinerary,
                "map": _map_summary(map_payload),
                "input_priority": ["raw_texts", "images", "urls"],
            },
            "system_directive": _system_directive(
                phase=phase,
                action=action,
                candidate_count=len(candidates),
                has_attachments=_has_attachments(context),
                has_url_failures=bool(_url_failures(state)),
                generic_food_additions=_generic_food_additions(user_added_places),
            ),
        }

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(args)
        if tool_name == "travel_search_poi":
            destination = _trip_meta(state, {}, _context_overrides(state)).get("destination")
            if destination and not normalized.get("city"):
                normalized["city"] = destination
            if "name_aliases" not in normalized and isinstance(normalized.get("aliases"), list):
                normalized["name_aliases"] = normalized.get("aliases")
            normalized.setdefault("page_size", 5)
        if tool_name == "travel_search_nearby_poi":
            normalized.setdefault("page_size", 5)
            normalized.setdefault("radius", 1500)
            if not normalized.get("location") and not (
                normalized.get("longitude") is not None and normalized.get("latitude") is not None
            ):
                poi = _first_verified_poi(state)
                if poi:
                    normalized["longitude"] = poi.get("longitude")
                    normalized["latitude"] = poi.get("latitude")
        if tool_name == "travel_fetch_url_content":
            normalized.setdefault("timeout_seconds", 8)
        if tool_name == "travel_create_personal_map":
            overrides = _context_overrides(state)
            trip_meta = _trip_meta(state, {}, overrides)
            normalized.setdefault("title", _map_title(trip_meta))
            if not isinstance(normalized.get("line_list"), list) or not normalized.get("line_list"):
                normalized["line_list"] = _line_list_from_payload(overrides)
            normalized.setdefault("scene_type", 1)
        return normalized

    def preview_tool_result(self, state: Any, tool_name: str, result: Any) -> Any | None:
        if tool_name == "travel_search_poi" and isinstance(result, dict):
            pois = _valid_pois(result)
            return {
                "status": "poi_verified" if pois else "poi_not_found",
                "query": result.get("query"),
                "count": len(pois),
                "names": [str(item.get("name")) for item in pois[:5] if item.get("name")],
            }
        if tool_name == "travel_search_nearby_poi" and isinstance(result, dict):
            pois = _valid_pois(result)
            return {
                "status": "nearby_poi_found" if pois else "nearby_poi_not_found",
                "query": result.get("query"),
                "count": len(pois),
                "names": [str(item.get("name")) for item in pois[:5] if item.get("name")],
            }
        if tool_name == "travel_fetch_url_content" and isinstance(result, dict):
            text = str(result.get("text") or "")
            return {
                "status": result.get("parse_status") or "failed",
                "url": result.get("url"),
                "title": result.get("title"),
                "text_chars": len(text),
                "error": result.get("error"),
            }
        if tool_name == "travel_create_personal_map" and isinstance(result, dict):
            return {
                "status": "map_generated" if result.get("qr_code_url") or result.get("schema_url") else "map_unavailable",
                "title": result.get("title"),
                "qr_code_url": result.get("qr_code_url"),
                "schema_url": result.get("schema_url"),
            }
        return None

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        if tool_name == "submit_final_answer":
            return _handle_submit_final_answer(state, action, result)

        if tool_name in {"travel_search_poi", "travel_search_nearby_poi"}:
            if action == "confirm_candidates":
                # 用户已确认候选，不再继续搜索 POI，直接生成行程
                candidates = _collect_verified_candidates(state)
                if candidates:
                    return _submit_final_for_confirmed(state, action, candidates)
                return _no_places_final(state, result)
            if _pending_verifiable_places(state):
                return None
            # 正常流程：收集候选并展示给用户确认
            candidates = _collect_verified_candidates(state)
            failed_places = _collect_failed_places(state)
            food_items = _collect_food_items(state)
            if not candidates and not failed_places and not food_items:
                return _no_places_final(state, result)
            return _candidate_confirmation_final(state, candidates)

        if tool_name == "travel_fetch_url_content" and isinstance(result, dict) and result.get("parse_status") == "failed":
            candidates = _collect_verified_candidates(state)
            if candidates:
                return _candidate_confirmation_final(state, candidates)
            return _url_fetch_failed_final(state, result)

        if tool_name == "travel_create_personal_map" and isinstance(result, dict):
            return _map_final(state, result)

        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        if getattr(state, "scene", "") != "travel_planner":
            return None
        candidates = _collect_verified_candidates(state)
        if candidates:
            return _candidate_confirmation_final(state, candidates)
        return {
            "state": "created",
            "await_confirmation": False,
            "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
            "sources": _sources(state),
            "places": [],
            "candidates": [],
            "itinerary": {"days": []},
            "map": {"qr_code_url": None, "schema_url": None},
            "raw_text": str(getattr(state, "message", "") or ""),
            "recommendations": [
                {
                    "title": "旅行规划还需要更多信息",
                    "reason": "请补充目的地、出行天数，并上传清晰攻略截图或粘贴攻略原文。",
                }
            ],
            "followups": ["补充攻略内容后，我会先提取并验证候选地点。"],
            "warnings": [],
        }

    def should_build_vision_input(self, state: Any) -> bool:
        context = getattr(state, "context", None)
        return _has_attachments(context if isinstance(context, dict) else {})

    def allow_submit_final_answer(self, state: Any) -> bool:
        if _pending_verifiable_places(state):
            return False
        return not _is_map_action(_travel_action(_context_overrides(state)))

    def short_circuit_final(self, state: Any) -> dict[str, Any] | None:
        if getattr(state, "scene", "") != "travel_planner":
            return None
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        candidates = _collect_verified_candidates(state)
        pending_places = _pending_verifiable_places(state)
        if action == "confirm_candidates" and candidates:
            return _submit_final_for_confirmed(state, action, candidates)
        if not action and not pending_places and (
            candidates or _collect_failed_places(state) or _collect_food_items(state)
        ):
            return _candidate_confirmation_final(state, candidates)
        return None

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]] | None:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        if not action:
            pending_places = _pending_verifiable_places(state)
            if pending_places:
                destination = _trip_meta(state, {}, overrides).get("destination")
                return [
                    {
                        "name": "travel_search_poi",
                        "args": {
                            "keywords": item.get("name"),
                            "city": destination,
                            "category": item.get("category"),
                            "name_aliases": item.get("aliases") or [],
                            "page_size": 5,
                        },
                        "type": "tool_call",
                    }
                    for item in pending_places[:8]
                    if item.get("name")
                ]
        if not _is_map_action(action):
            return None
        line_list = _line_list_from_payload(overrides)
        if not line_list:
            return None
        return [
            {
                "name": "travel_create_personal_map",
                "args": {
                    "title": _map_title(_trip_meta(state, {}, overrides)),
                    "line_list": line_list,
                    "scene_type": 1,
                },
                "type": "tool_call",
            }
        ]

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str] | None:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        trip_meta = _trip_meta(state, {}, overrides)
        phase = _phase(
            action=action,
            candidates=_collect_verified_candidates(state),
            itinerary=_payload_itinerary(overrides),
            map_payload=_latest_map_payload(state),
            trip_meta=trip_meta,
        )
        if _is_map_action(action):
            map_tools = [tool_name for tool_name in allowed_tools if tool_name == "travel_create_personal_map"]
            return map_tools or ["travel_create_personal_map"]
        if _pending_verifiable_places(state):
            poi_tools = [tool_name for tool_name in allowed_tools if tool_name == "travel_search_poi"]
            return poi_tools or ["travel_search_poi"]
        if phase != "map_generated":
            return [tool_name for tool_name in allowed_tools if tool_name != "travel_create_personal_map"]
        return None


def _context_overrides(state: Any) -> dict[str, Any]:
    overrides = getattr(state, "context_overrides", None)
    return overrides if isinstance(overrides, dict) else {}


def _travel_action(overrides: dict[str, Any]) -> str:
    value = overrides.get("travel_action")
    return str(value or "").strip()


def _is_map_action(action: str) -> bool:
    return action in {"confirm_itinerary", "generate_map", "confirm_plan"}


def _has_attachments(context: dict[str, Any]) -> bool:
    attachments = context.get("attachments")
    return isinstance(attachments, list) and bool(attachments)


def _trip_meta(state: Any, context: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    meta = payload.get("trip_meta") if isinstance(payload.get("trip_meta"), dict) else {}
    basic = payload.get("basic_info") if isinstance(payload.get("basic_info"), dict) else {}
    return {
        "destination": meta.get("destination") or basic.get("destination") or payload.get("destination"),
        "start_date": meta.get("start_date") or basic.get("start_date") or payload.get("start_date"),
        "end_date": meta.get("end_date") or basic.get("end_date") or payload.get("end_date"),
        "days": meta.get("days") or basic.get("days") or payload.get("days"),
        "travelers_count": meta.get("travelers_count") or basic.get("travelers_count") or payload.get("travelers_count"),
        "preferences": meta.get("preferences") or basic.get("preferences") or payload.get("preferences") or [],
        "budget": meta.get("budget") or basic.get("budget") or payload.get("budget"),
        "start_point": meta.get("start_point") or payload.get("start_point"),
        "end_point": meta.get("end_point") or payload.get("end_point"),
    }


def _phase(
    *,
    action: str,
    candidates: list[dict[str, Any]],
    itinerary: dict[str, Any],
    map_payload: dict[str, Any] | None,
    trip_meta: dict[str, Any] | None = None,
) -> str:
    if map_payload and (map_payload.get("qr_code_url") or map_payload.get("schema_url")):
        return "map_generated"
    if _is_map_action(action):
        return "itinerary_generated"
    if action == "confirm_candidates":
        return "candidates_confirmed"
    if isinstance(itinerary.get("days"), list) and itinerary.get("days"):
        return "itinerary_generated"
    if candidates:
        return "candidates_ready"
    if trip_meta and trip_meta.get("destination"):
        return "places_extracted"
    if trip_meta:
        return "created"
    return "ingesting_content"


def _system_directive(
    *,
    phase: str,
    action: str,
    candidate_count: int,
    has_attachments: bool,
    has_url_failures: bool,
    generic_food_additions: list[str],
) -> str:
    attachment_note = "当前有图片附件，必须先从图片中识别地点。" if has_attachments else "当前没有图片附件，优先使用用户文本中的攻略原文或 URL。"
    url_note = "已有 URL 获取失败记录；继续基于成功来源处理，并提示用户改用截图或粘贴原文。" if has_url_failures else ""
    food_note = ""
    if generic_food_additions:
        food_note = (
            "用户补充的美食地点不够具体："
            f"{'、'.join(generic_food_additions)}。必须先请用户补充可在高德检索的具体店名。"
        )
    if phase == "candidates_confirmed":
        return (
            "旅行规划候选已确认。现在只生成结构化 itinerary.days，并通过 submit_final_answer 返回 "
            "state=itinerary_generated、await_confirmation=true、trip_meta、candidates、itinerary.days、"
            "map={qr_code_url:null,schema_url:null}、raw_text。"
            "本阶段禁止调用 travel_create_personal_map；必须引导用户确认行程后再生成高德地图二维码。"
            f"{food_note}"
        )
    if phase == "itinerary_generated" and _is_map_action(action):
        return (
            "用户已确认最终行程。现在必须调用 travel_create_personal_map 生成高德二维码和 schema。"
            "只允许使用已验证 POI 构造 line_list；未验证地点不得进入地图点位。"
            "地图生成后返回 state=map_generated，并包含 itinerary.days、map.qr_code_url、map.schema_url。"
        )
    if phase == "itinerary_generated":
        return (
            "已生成每日行程。必须停在行程确认阶段，等待用户确认是否生成高德地图二维码；"
            "用户确认前禁止调用 travel_create_personal_map。"
        )
    if phase == "candidates_ready":
        return (
            f"已验证 {candidate_count} 个候选 POI。必须停在候选确认阶段，展示候选并等待用户确认；"
            "同时展示验证失败的地点和原因，引导用户增删地点；用户确认前禁止生成最终每日行程或高德地图。"
            f"{food_note}"
        )
    return (
        "旅行规划必须按 created -> ingesting_content -> places_extracted -> candidates_ready -> "
        "candidates_confirmed -> itinerary_generated -> map_generated 执行。"
        f"{attachment_note} 先提取地点，再用 travel_search_poi 验证 POI。"
        "如果用户上传了图片，你必须逐一识别图片中的景点、餐厅、酒店、商圈、车站等地点；"
        "图片清晰可读时禁止直接说无法识别图片；确实无法识别时先说明图片中可见内容并请求用户补充原文。"
        f"{url_note}{food_note}"
    )


def _sources(state: Any) -> list[dict[str, Any]]:
    overrides = _context_overrides(state)
    attachments = overrides.get("attachments")
    sources: list[dict[str, Any]] = []
    if isinstance(attachments, list):
        for item in attachments:
            if isinstance(item, dict):
                sources.append(
                    {
                        "type": "image",
                        "filename": item.get("filename"),
                        "content_type": item.get("content_type"),
                        "parse_status": "image_received",
                    }
                )
    observations = getattr(state, "observations", None)
    if isinstance(observations, list):
        for observation in observations:
            if not isinstance(observation, dict) or observation.get("tool") != "travel_fetch_url_content":
                continue
            result = observation.get("result")
            if not isinstance(result, dict):
                continue
            sources.append(
                {
                    "type": "url",
                    "url": result.get("url"),
                    "title": result.get("title"),
                    "parse_status": result.get("parse_status") or "failed",
                    "error": result.get("error"),
                }
            )
    return sources


def _url_failures(state: Any) -> list[dict[str, Any]]:
    observations = getattr(state, "observations", None)
    failures: list[dict[str, Any]] = []
    if not isinstance(observations, list):
        return failures
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") != "travel_fetch_url_content":
            continue
        result = observation.get("result")
        if isinstance(result, dict) and result.get("parse_status") == "failed":
            failures.append(result)
    return failures


def _valid_pois(result: dict[str, Any]) -> list[dict[str, Any]]:
    pois = result.get("pois")
    if not isinstance(pois, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in pois:
        if not isinstance(item, dict):
            continue
        if item.get("poi_id") and item.get("name") and item.get("longitude") is not None and item.get("latitude") is not None:
            valid.append(item)
    return valid


def _is_valid_selected_poi(item: dict[str, Any]) -> bool:
    return bool(item.get("poi_id") and item.get("name") and item.get("longitude") is not None and item.get("latitude") is not None)


def _candidate_reason(source_name: str, poi: dict[str, Any]) -> str:
    verified_name = str(poi.get("name") or "").strip()
    if source_name and verified_name and source_name != verified_name:
        return f"攻略提取「{source_name}」，已匹配高德 POI「{verified_name}」"
    return "已通过高德 POI 验证"


def _normalize_candidate(item: dict[str, Any]) -> dict[str, Any]:
    poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
    name = item.get("name") or poi.get("name")
    poi_name = poi.get("name") or name
    category = str(item.get("category") or _category_from_text(str(name or "")) or "attraction")
    score = item.get("score") or 8
    reason = item.get("reason") or "已通过高德 POI 验证"
    longitude = item.get("longitude") if item.get("longitude") is not None else poi.get("longitude")
    latitude = item.get("latitude") if item.get("latitude") is not None else poi.get("latitude")
    poi_id = item.get("poi_id") or item.get("amap_poi_id") or poi.get("poi_id") or poi.get("poiId")
    normalized = dict(item)
    normalized["candidate_id"] = normalized.get("candidate_id") or normalized.get("id")
    normalized["id"] = normalized.get("id") or normalized.get("candidate_id")
    normalized["name"] = name
    normalized["source_name"] = normalized.get("source_name") or name
    normalized["verified_name"] = normalized.get("verified_name") or poi_name
    normalized["category"] = category
    normalized["source"] = normalized.get("source") or "poi_verified"
    normalized["score"] = score
    normalized["score_breakdown"] = normalized.get("score_breakdown") or {
        "poi_verified": 3,
        "fit": 3,
        "route_value": 2,
    }
    normalized["reason"] = reason
    normalized["recommended_reason"] = normalized.get("recommended_reason") or reason
    normalized["not_recommended_reason"] = normalized.get("not_recommended_reason")
    normalized["recommended_time_slots"] = normalized.get("recommended_time_slots") or _recommended_time_slots(category)
    normalized["suggested_duration_minutes"] = normalized.get("suggested_duration_minutes") or _suggested_duration_minutes(category)
    normalized["best_visit_time"] = normalized.get("best_visit_time") or _best_visit_time(category)
    normalized["crowd_level"] = normalized.get("crowd_level") or "unknown"
    normalized["cost_estimate_yuan"] = normalized.get("cost_estimate_yuan")
    normalized["nearby_candidates"] = normalized.get("nearby_candidates") or []
    normalized["poi_verified"] = bool(poi_id and longitude is not None and latitude is not None)
    normalized["amap_poi_id"] = poi_id
    normalized["amap_poi_keyword"] = normalized.get("amap_poi_keyword") or name
    normalized["longitude"] = longitude
    normalized["latitude"] = latitude
    normalized["business_hours"] = normalized.get("business_hours") or poi.get("business_hours")
    normalized["tags"] = normalized.get("tags") or [category]
    normalized["warnings"] = normalized.get("warnings") or []
    normalized["poi"] = {
        "poi_id": poi_id,
        "name": poi_name,
        "address": normalized.get("address") or poi.get("address"),
        "longitude": longitude,
        "latitude": latitude,
    }
    return normalized


def _recommended_time_slots(category: str) -> list[str]:
    if category in {"restaurant", "food", "cafe"}:
        return ["lunch", "dinner"]
    if category in {"hotel"}:
        return ["evening"]
    return ["morning", "afternoon"]


def _suggested_duration_minutes(category: str) -> int:
    if category in {"restaurant", "food", "cafe"}:
        return 75
    if category in {"hotel"}:
        return 30
    if category in {"transport_hub"}:
        return 45
    return 120


def _best_visit_time(category: str) -> str:
    if category in {"restaurant", "food", "cafe"}:
        return "午餐或晚餐时段"
    if category == "hotel":
        return "晚间入住"
    return "白天游览"


def _collect_verified_candidates(state: Any) -> list[dict[str, Any]]:
    payload_candidates = _payload_candidates(_context_overrides(state))
    observations = getattr(state, "observations", None)
    candidates: list[dict[str, Any]] = list(payload_candidates)
    seen: set[str] = {
        str(
            (((item.get("poi") or {}) if isinstance(item.get("poi"), dict) else {}).get("poi_id"))
            or item.get("poi_id")
            or item.get("name")
        )
        for item in candidates
        if isinstance(item, dict)
    }
    if not isinstance(observations, list):
        return candidates
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") not in {"travel_search_poi", "travel_search_nearby_poi"}:
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        query = result.get("query") if isinstance(result.get("query"), dict) else {}
        selected_poi = result.get("selected_poi") if isinstance(result.get("selected_poi"), dict) else None
        if selected_poi is None:
            valid_pois = _valid_pois(result)
            selected_poi = valid_pois[0] if valid_pois else None
        if not selected_poi or not _is_valid_selected_poi(selected_poi):
            continue
        key = str(selected_poi.get("poi_id") or selected_poi.get("name") or query.get("keywords"))
        if key in seen:
            continue
        seen.add(key)
        source_name = str(result.get("source_name") or query.get("keywords") or selected_poi.get("name") or "").strip()
        candidates.append(
            _normalize_candidate(
                {
                    "candidate_id": f"candidate_{len(candidates) + 1:03d}",
                    "name": source_name or selected_poi.get("name"),
                    "source_name": source_name or selected_poi.get("name"),
                    "verified_name": selected_poi.get("name"),
                    "category": query.get("category") or _category_from_query(query),
                    "source": "poi_verified",
                    "score": 8,
                    "reason": _candidate_reason(source_name, selected_poi),
                    "amap_poi_keyword": query.get("keywords"),
                    "match_status": result.get("match_status"),
                    "rejected_pois": result.get("rejected_pois") if isinstance(result.get("rejected_pois"), list) else [],
                    "poi": {
                        "poi_id": selected_poi.get("poi_id"),
                        "name": selected_poi.get("name"),
                        "address": selected_poi.get("address"),
                        "longitude": selected_poi.get("longitude"),
                        "latitude": selected_poi.get("latitude"),
                    },
                }
            )
        )
    excluded = _payload_excluded_places(_context_overrides(state))
    if excluded:
        candidates = [item for item in candidates if not _matches_excluded(item, excluded)]
    return candidates


def _payload_candidates(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    payload = overrides.get("travel_payload")
    if not isinstance(payload, dict):
        return []
    raw_candidates = payload.get("candidates") or payload.get("confirmed_candidates")
    if not isinstance(raw_candidates, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        poi = item.get("poi") if isinstance(item.get("poi"), dict) else item
        name = item.get("name") or poi.get("name")
        poi_id = item.get("poi_id") or poi.get("poi_id")
        longitude = item.get("longitude") if item.get("longitude") is not None else poi.get("longitude")
        latitude = item.get("latitude") if item.get("latitude") is not None else poi.get("latitude")
        if not name:
            continue
        candidates.append(
            _normalize_candidate({
                "candidate_id": item.get("candidate_id") or f"candidate_{len(candidates) + 1:03d}",
                "name": name,
                "category": item.get("category") or "attraction",
                "source": item.get("source") or "user_confirmed",
                "score": item.get("score") or 9,
                "reason": item.get("reason") or "用户已确认候选地点",
                "poi": {
                    "poi_id": poi_id,
                    "name": name,
                    "address": item.get("address") or poi.get("address"),
                    "longitude": longitude,
                    "latitude": latitude,
                },
            })
        )
    seen = {_candidate_key(item) for item in candidates}
    for added in _payload_user_added_places(overrides):
        key = _candidate_key(added)
        if key in seen:
            for existing in candidates:
                if _candidate_key(existing) == key:
                    existing["source"] = "user_added"
                    existing["score"] = 10
                    existing["reason"] = "用户补充地点，最高优先级"
            continue
        seen.add(key)
        candidates.append(
            _normalize_candidate({
                "candidate_id": added.get("candidate_id") or f"candidate_{len(candidates) + 1:03d}",
                "name": added.get("name"),
                "category": added.get("category") or "attraction",
                "source": "user_added",
                "score": 10,
                "reason": added.get("reason") or "用户补充地点，最高优先级",
                "poi": {
                    "poi_id": added.get("poi_id"),
                    "name": added.get("name"),
                    "address": added.get("address"),
                    "longitude": added.get("longitude"),
                    "latitude": added.get("latitude"),
                },
            })
        )
    excluded = _payload_excluded_places(overrides)
    if excluded:
        candidates = [item for item in candidates if not _matches_excluded(item, excluded)]
    return candidates


def _payload_user_added_places(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    raw_items = payload.get("user_added_places") or payload.get("added_places") or payload.get("additions") or []
    if not isinstance(raw_items, list):
        raw_items = [raw_items]
    places: list[dict[str, Any]] = []
    for item in raw_items:
        normalized = _normalize_user_added_place(item)
        if normalized:
            places.append(normalized)
    return places


def _normalize_user_added_place(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        name = item.strip()
        return {"name": name, "category": _category_from_text(name)} if name else None
    if not isinstance(item, dict):
        return None
    poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
    name = str(item.get("name") or poi.get("name") or "").strip()
    if not name:
        return None
    longitude = item.get("longitude") if item.get("longitude") is not None else poi.get("longitude")
    latitude = item.get("latitude") if item.get("latitude") is not None else poi.get("latitude")
    return {
        "candidate_id": item.get("candidate_id"),
        "name": name,
        "category": item.get("category") or _category_from_text(name),
        "reason": item.get("reason"),
        "poi_id": item.get("poi_id") or poi.get("poi_id"),
        "address": item.get("address") or poi.get("address"),
        "longitude": longitude,
        "latitude": latitude,
    }


def _payload_excluded_places(overrides: dict[str, Any]) -> list[str]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    raw_items = (
        payload.get("excluded_places")
        or payload.get("removed_places")
        or payload.get("deleted_places")
        or payload.get("delete_places")
        or []
    )
    if not isinstance(raw_items, list):
        raw_items = [raw_items]
    names: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            value = item.get("candidate_id") or item.get("name")
        else:
            value = item
        name = str(value or "").strip()
        if name:
            names.append(name)
    return names


def _candidate_key(item: dict[str, Any]) -> str:
    poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
    return str(item.get("candidate_id") or poi.get("poi_id") or item.get("poi_id") or item.get("name") or "").strip()


def _matches_excluded(item: dict[str, Any], excluded: list[str]) -> bool:
    key = _candidate_key(item)
    name = str(item.get("name") or "").strip()
    return any(value and (value == key or value == name) for value in excluded)


def _payload_itinerary(overrides: dict[str, Any]) -> dict[str, Any]:
    payload = overrides.get("travel_payload")
    if not isinstance(payload, dict):
        return {"days": []}
    itinerary = payload.get("itinerary")
    if isinstance(itinerary, dict):
        days = itinerary.get("days")
        return {"days": days if isinstance(days, list) else []}
    days = payload.get("days")
    if isinstance(days, list):
        return {"days": days}
    return {"days": []}


def _collect_extracted_places(state: Any) -> list[dict[str, Any]]:
    overrides = _context_overrides(state)
    payload = overrides.get("travel_payload") if isinstance(overrides.get("travel_payload"), dict) else {}
    raw_places = payload.get("extracted_places") or payload.get("places") or []
    places = _normalize_extracted_places(raw_places)
    content = _last_ai_message_content(state)
    places.extend(_extract_places_from_ai_content(content))
    return _dedupe_places(places)


def _collect_food_items(state: Any) -> list[dict[str, Any]]:
    overrides = _context_overrides(state)
    payload = overrides.get("travel_payload") if isinstance(overrides.get("travel_payload"), dict) else {}
    raw_items = payload.get("food_items") or payload.get("food_preferences") or []
    items = _normalize_food_items(raw_items)
    items.extend(_extract_food_items_from_ai_content(_last_ai_message_content(state)))
    for place in _collect_extracted_places(state):
        name = str(place.get("name") or "").strip()
        if place.get("category") in {"restaurant", "food", "cafe", "nightlife"} and _is_generic_food_name(name):
            items.append({"name": name, "category": "food", "source": place.get("source") or "image_extracted"})
    return _dedupe_places(items)


def _pending_verifiable_places(state: Any) -> list[dict[str, Any]]:
    processed = _processed_poi_names(state)
    pending: list[dict[str, Any]] = []
    for place in _collect_extracted_places(state):
        name = str(place.get("name") or "").strip()
        if not name or _is_generic_food_name(name):
            continue
        category = str(place.get("category") or _category_from_text(name))
        if category in {"food"} and _is_generic_food_name(name):
            continue
        aliases = [str(item).strip() for item in place.get("aliases", []) if str(item).strip()] if isinstance(place.get("aliases"), list) else []
        keys = {_normalize_name_key(name), *[_normalize_name_key(item) for item in aliases]}
        if keys and keys.intersection(processed):
            continue
        pending.append({**place, "category": category, "aliases": aliases})
    return pending


def _processed_poi_names(state: Any) -> set[str]:
    processed: set[str] = set()
    observations = getattr(state, "observations", None)
    if not isinstance(observations, list):
        return processed
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") not in {"travel_search_poi", "travel_search_nearby_poi"}:
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        query = result.get("query") if isinstance(result.get("query"), dict) else {}
        for value in (
            result.get("source_name"),
            result.get("keywords"),
            result.get("name"),
            query.get("keywords"),
        ):
            key = _normalize_name_key(str(value or ""))
            if key:
                processed.add(key)
        aliases = query.get("name_aliases") or result.get("name_aliases")
        if isinstance(aliases, list):
            for item in aliases:
                key = _normalize_name_key(str(item or ""))
                if key:
                    processed.add(key)
    return processed


def _last_ai_message_content(state: Any) -> str:
    skill_state = getattr(state, "skill_state", None)
    if not isinstance(skill_state, dict):
        return ""
    contents = skill_state.get("ai_message_contents")
    if isinstance(contents, list) and contents:
        return "\n\n".join(str(item) for item in contents if str(item).strip())
    return str(skill_state.get("last_ai_message_content") or "")


def _normalize_extracted_places(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        raw_items = [raw_items] if raw_items else []
    places: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                places.append({"name": name, "category": _category_from_text(name), "source": "payload"})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("source_name") or item.get("title") or "").strip()
        if not name:
            continue
        aliases = item.get("aliases") or item.get("name_aliases") or []
        places.append(
            {
                "name": name,
                "category": item.get("category") or _category_from_text(name),
                "aliases": aliases if isinstance(aliases, list) else [],
                "context_snippet": item.get("context_snippet") or item.get("description"),
                "source": item.get("source") or "payload",
            }
        )
    return places


def _normalize_food_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        raw_items = [raw_items] if raw_items else []
    foods: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                foods.append({"name": name, "category": "food", "source": "payload"})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        if name:
            foods.append({**item, "name": name, "category": item.get("category") or "food"})
    return foods


def _extract_places_from_ai_content(content: str) -> list[dict[str, Any]]:
    if not content:
        return []
    segmented = _extract_segmented_places_from_ai_content(content)
    if segmented:
        return segmented
    section = _section_between(content, ("识别到的地点", "地点"), ("美食推荐", "现在我调用", "调用高德", "接下来"))
    if not section:
        return []
    places: list[dict[str, Any]] = []
    for line in section.splitlines():
        text = line.strip()
        if not text:
            continue
        text = re.sub(r"^(?:[-*]|\d+[.、])\s*", "", text)
        bold_match = re.search(r"\*\*([^*]+)\*\*", text)
        if bold_match:
            raw_name = bold_match.group(1)
            description = text[bold_match.end() :]
        else:
            parts = re.split(r"\s[-—:：]\s|[-—:：]", text, maxsplit=1)
            raw_name = parts[0]
            description = parts[1] if len(parts) > 1 else ""
        name = _clean_extracted_name(raw_name)
        if not name:
            continue
        description = str(description or "").strip()
        places.append(
            {
                "name": name,
                "category": _category_from_text(f"{name} {description}"),
                "context_snippet": description,
                "source": "image_extracted",
            }
        )
    return places


def _extract_food_items_from_ai_content(content: str) -> list[dict[str, Any]]:
    if not content:
        return []
    segmented = [
        item
        for item in _extract_segmented_places_from_ai_content(content)
        if item.get("category") == "food"
    ]
    if segmented:
        return segmented
    section = _section_between(content, ("美食推荐", "美食"), ("现在我调用", "调用高德", "接下来", "已验证"))
    if not section:
        return []
    names: list[str] = []
    for line in section.splitlines():
        text = line.strip().lstrip("-*0123456789.、 ")
        if not text:
            continue
        for part in re.split(r"[、,，/]+", text):
            name = _clean_extracted_name(part)
            if name:
                names.append(name)
    return [{"name": name, "category": "food", "source": "image_extracted"} for name in names]


def _extract_segmented_places_from_ai_content(content: str) -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []
    current_category: str | None = None
    for line in content.splitlines():
        text = line.strip()
        if not text:
            continue
        heading = re.sub(r"[*#：:\s]+", "", text)
        if "景点类" in heading:
            current_category = "attraction"
            continue
        if "美食类" in heading or "美食推荐" in heading:
            current_category = "restaurant"
            continue
        if "住宿类" in heading or "酒店" in heading:
            current_category = "hotel"
            continue
        if "交通类" in heading:
            current_category = "transport_hub"
            continue
        if current_category is None:
            continue
        if re.search(r"现在.*(?:高德|POI|验证|搜索)|调用.*(?:高德|POI|验证|搜索)", text):
            break
        item = _extract_list_item_name(text)
        if not item:
            continue
        category = "food" if current_category == "restaurant" and _is_generic_food_name(item["name"]) else current_category
        places.append(
            {
                "name": item["name"],
                "category": category,
                "context_snippet": item.get("description"),
                "source": "image_extracted",
            }
        )
    return places


def _extract_list_item_name(text: str) -> dict[str, str] | None:
    normalized = re.sub(r"^(?:[-*]|\d+[.、])\s*", "", text.strip())
    if not normalized:
        return None
    bold_match = re.search(r"\*\*([^*]+)\*\*", normalized)
    if bold_match:
        raw_name = bold_match.group(1)
        description = normalized[bold_match.end() :]
    else:
        parts = re.split(r"\s[-—:：]\s|[-—:：]", normalized, maxsplit=1)
        raw_name = parts[0]
        description = parts[1] if len(parts) > 1 else ""
    name = _clean_extracted_name(raw_name)
    if not name:
        return None
    return {"name": name, "description": str(description or "").strip()}


def _section_between(content: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
    start_index = -1
    for token in starts:
        found = content.find(token)
        if found >= 0 and (start_index < 0 or found < start_index):
            start_index = found
    if start_index < 0:
        return ""
    section = content[start_index:]
    first_line_break = section.find("\n")
    if first_line_break >= 0:
        section = section[first_line_break + 1 :]
    end_index = len(section)
    for token in ends:
        found = section.find(token)
        if found >= 0:
            end_index = min(end_index, found)
    return section[:end_index]


def _clean_extracted_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[#>\s\-*`]+", "", text)
    text = re.sub(r"[🐼🏛️🛍️🌳🏯🌙🌿✅❌⚠️]+", "", text).strip()
    text = text.strip("*`：:-— ")
    text = re.sub(r"\s+", "", text)
    return text[:40]


def _dedupe_places(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        key = _normalize_name_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _normalize_name_key(value: str) -> str:
    return re.sub(r"[\s（）()【】\\[\\]·•、,，。:：;；\\-_/]+", "", str(value or "").lower())


def _collect_failed_places(state: Any) -> list[dict[str, Any]]:
    observations = getattr(state, "observations", None)
    if not isinstance(observations, list):
        return []
    failed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") not in {"travel_search_poi", "travel_search_nearby_poi"}:
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        selected_poi = result.get("selected_poi") if isinstance(result.get("selected_poi"), dict) else None
        if selected_poi is None:
            valid_pois = _valid_pois(result)
            selected_poi = valid_pois[0] if valid_pois else None
        if selected_poi and _is_valid_selected_poi(selected_poi):
            continue
        query = result.get("query") if isinstance(result.get("query"), dict) else {}
        name = str(query.get("keywords") or result.get("keywords") or result.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rejected_pois = result.get("rejected_pois") if isinstance(result.get("rejected_pois"), list) else []
        reason = result.get("error") or _match_failure_reason(result.get("match_status"), rejected_pois)
        failed.append(
            {
                "name": name,
                "source_name": name,
                "category": query.get("category") or _category_from_query(query),
                "city": query.get("city"),
                "reason": reason,
                "rejected_pois": rejected_pois,
            }
        )
    return failed


def _match_failure_reason(match_status: Any, rejected_pois: list[Any]) -> str:
    if match_status == "only_transport_affix":
        return "只匹配到地铁站、公交站、停车场或出入口，不是攻略地点本体"
    if rejected_pois:
        return "未找到与攻略地点名称足够一致的高德 POI"
    return "未在高德 POI 中验证到有效坐标"


def _category_from_query(query: dict[str, Any]) -> str:
    text = " ".join(str(query.get(key) or "") for key in ("keywords", "types")).lower()
    if any(token in text for token in ("餐", "饭", "美食", "咖啡", "restaurant", "food", "cafe")):
        return "restaurant"
    if any(token in text for token in ("酒店", "住宿", "hotel")):
        return "hotel"
    return "attraction"


def _category_from_text(text: str) -> str:
    value = str(text or "").lower()
    if any(token in value for token in ("餐", "饭", "美食", "咖啡", "拉面", "火锅", "restaurant", "food", "cafe")):
        return "restaurant"
    if any(token in value for token in ("酒店", "住宿", "民宿", "hotel")):
        return "hotel"
    if any(token in value for token in ("机场", "车站", "火车站", "地铁", "transport")):
        return "transport_hub"
    return "attraction"


def _generic_food_additions(user_added_places: list[dict[str, Any]]) -> list[str]:
    generic: list[str] = []
    for item in user_added_places:
        category = str(item.get("category") or "")
        name = str(item.get("name") or "").strip()
        if category in {"restaurant", "cafe", "nightlife", "food"} and _is_generic_food_name(name):
            generic.append(name)
    return generic


def _is_generic_food_name(name: str) -> bool:
    value = str(name or "").strip()
    if not value:
        return False
    generic_exact = {
        "美食",
        "当地美食",
        "特色美食",
        "午餐",
        "晚餐",
        "早餐",
        "餐厅",
        "饭店",
        "小吃",
        "咖啡",
        "火锅",
        "烧烤",
        "面馆",
        "夜市",
        "甜品",
        "奶茶",
        "烤肉",
        "串串",
        "自助餐",
        "农家菜",
        "当地菜",
    }
    if value in generic_exact:
        return True
    return any(token in value for token in ("附近", "随便", "吃饭", "吃点", "好吃的"))


def _first_verified_poi(state: Any) -> dict[str, Any] | None:
    for candidate in _collect_verified_candidates(state):
        poi = candidate.get("poi") if isinstance(candidate.get("poi"), dict) else {}
        if poi.get("longitude") is not None and poi.get("latitude") is not None:
            return poi
    return None


def _latest_map_payload(state: Any) -> dict[str, Any] | None:
    observations = getattr(state, "observations", None)
    if not isinstance(observations, list):
        return None
    for observation in reversed(observations):
        if isinstance(observation, dict) and observation.get("tool") == "travel_create_personal_map":
            result = observation.get("result")
            return result if isinstance(result, dict) else None
    return None


def _map_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"qr_code_url": None, "schema_url": None}
    return {
        "qr_code_url": result.get("qr_code_url"),
        "schema_url": result.get("schema_url"),
        "title": result.get("title"),
    }


def _candidate_markdown(
    candidates: list[dict[str, Any]],
    failed_places: list[dict[str, Any]],
    food_items: list[dict[str, Any]],
    groups: dict[str, Any] | None = None,
    extracted_places: list[dict[str, Any]] | None = None,
) -> str:
    groups = groups or _candidate_groups(candidates, failed_places, food_items)
    payload = {
        "total_candidates": len(candidates),
        "categories": _candidate_categories(candidates),
        "extracted_places": extracted_places or [],
        "candidates": candidates,
        "failed_places": failed_places,
        "food_items": food_items,
        "candidate_groups": groups,
    }
    lines = [
        "## 候选地点筛选结果",
        "",
        f"- 已验证候选：{len(candidates)} 个",
        f"- 验证失败 / 需确认：{len(failed_places)} 个",
        f"- 美食偏好 / 未验证菜品：{len(food_items)} 个",
        "",
    ]
    if candidates:
        lines.append("### 已验证候选 POI")
        for index, item in enumerate(candidates, start=1):
            poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
            source_name = item.get("source_name") or item.get("name") or poi.get("name") or "未命名地点"
            verified_name = item.get("verified_name") or poi.get("name") or source_name
            address = str(poi.get("address") or item.get("address") or "").strip()
            category = str(item.get("category") or "").strip()
            lines.append(f"{index}. **{source_name}**")
            lines.append(f"   - category: `{category or 'unknown'}`")
            lines.append(f"   - 高德匹配：{verified_name}")
            if address:
                lines.append(f"   - 地址：{address}")
    if failed_places:
        lines.append("\n### 验证失败 / 需确认")
        for index, item in enumerate(failed_places, start=1):
            name = item.get("source_name") or item.get("name") or "未知地点"
            reason = item.get("reason") or "未验证通过"
            lines.append(f"{index}. **{name}**：{reason}")
    if food_items:
        lines.append("\n### 美食偏好 / 未验证菜品")
        for index, item in enumerate(food_items, start=1):
            lines.append(f"{index}. {item.get('name') or '未命名美食'}")
    lines.extend(
        [
            "",
            "### curate-trip-candidates JSON",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "请确认、删除或补充候选地点，确认后我再生成每日行程。",
        ]
    )
    return "\n".join(lines).strip()


def _itinerary_markdown(days: list[dict[str, Any]]) -> str:
    lines = ["## 每日行程草稿"]
    if not days:
        lines.append("暂未生成可展示的每日行程。")
        return "\n".join(lines)
    for day_index, day in enumerate(days, start=1):
        if not isinstance(day, dict):
            continue
        title = day.get("theme") or day.get("title") or f"Day {day.get('day_number') or day_index}"
        lines.append(f"\n### {title}")
        items = day.get("items")
        if not isinstance(items, list):
            continue
        for order, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            name = item.get("place_name") or item.get("name") or item.get("title") or "未命名地点"
            time_text = item.get("time") or item.get("time_range")
            prefix = f"{time_text} " if time_text else ""
            lines.append(f"{order}. {prefix}{name}")
            transport = item.get("transport_to_next")
            if isinstance(transport, dict):
                summary = "，".join(
                    str(value)
                    for value in (
                        transport.get("mode"),
                        transport.get("duration"),
                        transport.get("distance"),
                        transport.get("notes"),
                    )
                    if value
                )
                if summary:
                    lines.append(f"   - 下一段路线：{summary}")
    lines.append("\n确认这版行程后，我会生成高德地图二维码。")
    return "\n".join(lines).strip()


def _map_markdown(result: dict[str, Any], days: list[dict[str, Any]], route_preview: list[dict[str, Any]]) -> str:
    lines = ["## 旅行计划已生成"]
    if result.get("schema_url"):
        lines.append("高德地图链接已生成，可在详情页查看二维码。")
    elif result.get("error"):
        lines.append(f"高德地图生成失败：{result.get('message') or result.get('error')}")
    if route_preview:
        lines.append("\n## 路线预览")
        for route in route_preview[:6]:
            if not isinstance(route, dict):
                continue
            title = route.get("title") or route.get("destination") or "路线"
            lines.append(f"- **{title}**")
            points = route.get("points")
            if isinstance(points, list) and points:
                names = [str(point.get("name") or "") for point in points if isinstance(point, dict) and point.get("name")]
                if names:
                    lines.append(f"  - {' -> '.join(names)}")
    if days:
        lines.append("\n" + _itinerary_markdown(days))
    return "\n".join(lines).strip()


def _candidate_categories(candidates: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in candidates:
        category = str(item.get("category") or "unknown")
        summary[category] = summary.get(category, 0) + 1
    return summary


def _candidate_groups(
    candidates: list[dict[str, Any]],
    failed_places: list[dict[str, Any]],
    food_items: list[dict[str, Any]],
) -> dict[str, Any]:
    groups: dict[str, Any] = {
        "attractions": [],
        "restaurants": [],
        "hotels": [],
        "transport_hubs": [],
        "others": [],
        "food_items": food_items,
        "failed": failed_places,
        "excluded": failed_places,
    }
    for item in candidates:
        category = str(item.get("category") or "")
        if category in {"restaurant", "food", "cafe"}:
            groups["restaurants"].append(item)
        elif category == "hotel":
            groups["hotels"].append(item)
        elif category == "transport_hub":
            groups["transport_hubs"].append(item)
        elif category == "attraction":
            groups["attractions"].append(item)
        else:
            groups["others"].append(item)
    return groups


def _candidate_confirmation_final(state: Any, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    failed_places = _collect_failed_places(state)
    food_items = _collect_food_items(state)
    extracted_places = _collect_extracted_places(state)
    followups = ["确认候选地点后，将继续生成最终每日行程。"]
    if failed_places:
        followups.insert(0, "部分地点未验证通过，你可以补充准确名称、删除或改成附近地标。")
    groups = _candidate_groups(candidates, failed_places, food_items)
    raw_text = _candidate_markdown(candidates, failed_places, food_items, groups, extracted_places)
    return {
        "state": "candidates_ready",
        "await_confirmation": True,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": extracted_places or [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
        "total_candidates": len(candidates),
        "categories": _candidate_categories(candidates),
        "candidate_groups": groups,
        "candidates": candidates,
        "failed_places": failed_places,
        "food_items": food_items,
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": raw_text,
        "recommendations": [
            {
                "title": f"已验证 {len(candidates)} 个候选地点",
                "reason": "请先确认、删除或补充候选地点，确认后我再生成每日行程。",
            }
        ],
        "followups": followups,
        "warnings": [f"{item.get('name')}：{item.get('reason')}" for item in failed_places],
    }


def _no_places_final(state: Any, result: Any) -> dict[str, Any]:
    warnings = ["未能从当前攻略内容中验证出有效 POI，请补充更清晰截图或粘贴攻略原文。"]
    if isinstance(result, dict) and result.get("error"):
        warnings.append(str(result.get("error")))
    return {
        "state": "places_extracted",
        "await_confirmation": False,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [],
        "candidates": [],
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {
                "title": "暂未识别到可用地点",
                "reason": "我没有拿到可用于高德验证的地点名称。",
            }
        ],
        "followups": ["请上传更清晰的攻略图片，或直接粘贴攻略原文中的地点列表。"],
        "warnings": warnings,
    }


def _url_fetch_failed_final(state: Any, result: dict[str, Any]) -> dict[str, Any]:
    error = str(result.get("error") or "URL 内容获取失败")
    url = str(result.get("url") or "")
    return {
        "state": "ingesting_content",
        "await_confirmation": False,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [],
        "candidates": [],
        "failed_places": [],
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {
                "title": "攻略链接暂时无法读取",
                "reason": "我会跳过失败链接；为了提高成功率，建议上传攻略截图或直接粘贴攻略原文。",
            }
        ],
        "followups": ["请补充截图或攻略原文后，我会继续提取候选地点。"],
        "warnings": [f"{url}：{error}" if url else error],
    }


def _map_final(state: Any, result: dict[str, Any]) -> dict[str, Any]:
    line_list = result.get("line_list") if isinstance(result.get("line_list"), list) else []
    days = _days_from_line_list(line_list)
    overrides = _context_overrides(state)
    payload_days = _payload_itinerary(overrides).get("days")
    if not days and isinstance(payload_days, list):
        days = payload_days
    route_preview = _collect_route_observations(state) or _route_preview_from_line_list(line_list)
    raw_text = _map_markdown(result, days, route_preview)
    return {
        "state": "map_generated",
        "await_confirmation": False,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [],
        "candidates": _collect_verified_candidates(state),
        "itinerary": {"days": days},
        "routes": route_preview,
        "map": {
            "qr_code_url": result.get("qr_code_url"),
            "schema_url": result.get("schema_url"),
            "title": result.get("title"),
            "line_list": line_list,
            "route_preview": route_preview,
        },
        "raw_text": raw_text,
        "recommendations": [
            {
                "title": result.get("title") or "旅行计划已生成",
                "reason": "已完成每日行程并生成高德个人地图。",
            }
        ],
        "followups": ["你可以继续提出调整要求，我会在当前计划基础上修改。"],
        "warnings": [] if result.get("qr_code_url") or result.get("schema_url") else ["高德地图二维码暂不可用，请稍后重试。"],
    }


def _collect_route_observations(state: Any) -> list[dict[str, Any]]:
    observations = getattr(state, "observations", None)
    if not isinstance(observations, list):
        return []
    routes: list[dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") != "plan_route":
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        routes.append(
            {
                "origin": result.get("origin"),
                "destination": result.get("destination"),
                "distance_m": result.get("distance_m"),
                "duration_s": result.get("duration_s"),
                "mode": result.get("mode"),
                "steps": result.get("steps") or result.get("segments") or [],
            }
        )
    return routes


def _route_preview_from_line_list(line_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for day_index, line in enumerate(line_list, start=1):
        points = line.get("pointInfoList") if isinstance(line.get("pointInfoList"), list) else []
        legs: list[dict[str, Any]] = []
        for index in range(len(points) - 1):
            origin = points[index]
            destination = points[index + 1]
            if not isinstance(origin, dict) or not isinstance(destination, dict):
                continue
            legs.append(
                {
                    "from": origin.get("name"),
                    "to": destination.get("name"),
                    "origin": {"longitude": origin.get("lon"), "latitude": origin.get("lat")},
                    "destination": {"longitude": destination.get("lon"), "latitude": destination.get("lat")},
                    "mode": "walking_or_driving",
                    "status": "preview_only",
                }
            )
        preview.append(
            {
                "day_number": day_index,
                "title": line.get("title") or f"Day {day_index}",
                "points": [
                    {
                        "name": point.get("name"),
                        "poi_id": point.get("poiId"),
                        "longitude": point.get("lon"),
                        "latitude": point.get("lat"),
                    }
                    for point in points
                    if isinstance(point, dict)
                ],
                "legs": legs,
            }
        )
    return preview


def _submit_final_for_confirmed(state: Any, action: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """用户已确认候选地点，直接生成行程."""
    overrides = _context_overrides(state)
    trip_meta = _trip_meta(state, {}, overrides)
    llm_days = _payload_itinerary(overrides).get("days")
    if not isinstance(llm_days, list) or not llm_days:
        llm_days = _build_rough_itinerary(candidates, trip_meta).get("days", [])
    raw_text = _itinerary_markdown(llm_days)
    return {
        "state": "itinerary_generated",
        "await_confirmation": True,
        "trip_meta": trip_meta,
        "sources": _sources(state),
        "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
        "candidates": candidates,
        "itinerary": {"days": llm_days},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": raw_text,
        "recommendations": [
            {"title": "行程已生成", "reason": "请确认最终行程，确认后可生成高德地图二维码。"}
        ],
        "followups": ["确认行程后即可生成高德地图二维码。"],
        "warnings": [],
    }


def _handle_submit_final_answer(state: Any, action: str, result: Any) -> dict[str, Any] | None:
    """当 LLM 调用 submit_final_answer 时，根据当前阶段返回结构化数据."""
    final_answer = result.get("_final_answer") if isinstance(result, dict) else {}
    if not isinstance(final_answer, dict):
        final_answer = {}

    overrides = _context_overrides(state)
    candidates = _collect_verified_candidates(state)
    trip_meta = _trip_meta(state, {}, overrides)
    sources = _sources(state)

    # 从 LLM 的输出中提取 itinerary，如果 LLM 没生成就用候选数据构造
    itinerary = final_answer.get("itinerary") if isinstance(final_answer.get("itinerary"), dict) else _payload_itinerary(overrides)
    llm_days = itinerary.get("days") if isinstance(itinerary, dict) and isinstance(itinerary.get("days"), list) else []

    if action == "confirm_itinerary" or action == "generate_map":
        final_days = llm_days if llm_days else _build_rough_itinerary(candidates, trip_meta).get("days", [])
        return {
            "state": "itinerary_generated",
            "await_confirmation": True,
            "trip_meta": trip_meta,
            "sources": sources,
            "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
            "candidates": candidates,
            "itinerary": {"days": final_days},
            "map": {"qr_code_url": None, "schema_url": None},
            "raw_text": _itinerary_markdown(final_days),
            "recommendations": [
                {"title": "行程已生成", "reason": "请确认最终行程，确认后可生成高德地图二维码。"}
            ],
            "followups": ["确认行程后即可生成高德地图二维码。", "你可以继续调整行程内容。"],
            "warnings": [],
        }

    if action == "confirm_candidates":
        final_days = llm_days if llm_days else _build_rough_itinerary(candidates, trip_meta).get("days", [])
        return {
            "state": "itinerary_generated",
            "await_confirmation": True,
            "trip_meta": trip_meta,
            "sources": sources,
            "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
            "candidates": candidates,
            "itinerary": {"days": final_days},
            "map": {"qr_code_url": None, "schema_url": None},
            "raw_text": _itinerary_markdown(final_days),
            "recommendations": [
                {"title": "行程已生成", "reason": "请确认最终行程，确认后可生成高德地图二维码。"}
            ],
            "followups": ["确认行程后即可生成高德地图二维码。"],
            "warnings": [],
        }

    # 默认：返回现有的候选确认状态
    if candidates:
        return _candidate_confirmation_final(state, candidates)
    return _no_places_final(state, {})


def _build_rough_itinerary(candidates: list[dict[str, Any]], trip_meta: dict[str, Any]) -> dict[str, Any]:
    """从候选列表构造粗略行程."""
    try:
        day_count = int(trip_meta.get("days") or 1)
    except (TypeError, ValueError):
        day_count = 1
    day_count = max(1, min(day_count, 15))
    if not candidates:
        return {"days": []}
    per_day = max(1, len(candidates) // day_count)
    days = []
    for day_index in range(day_count):
        start = day_index * per_day
        end = start + per_day if day_index < day_count - 1 else len(candidates)
        day_candidates = candidates[start:end]
        items = []
        for item in day_candidates:
            poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
            transport_to_next = None
            if len(day_candidates) > 1 and item is not day_candidates[-1]:
                transport_to_next = {
                    "mode": "按地图导航",
                    "duration": "以高德实时规划为准",
                    "distance": "以高德实时规划为准",
                    "notes": "已保留 POI 坐标，生成地图后可查看导航路线。",
                }
            row = {
                "place_name": item.get("name") or poi.get("name", ""),
                "name": item.get("name") or poi.get("name", ""),
                "poi_id": poi.get("poi_id"),
                "longitude": poi.get("longitude"),
                "latitude": poi.get("latitude"),
                "address": poi.get("address"),
            }
            if transport_to_next:
                row["transport_to_next"] = transport_to_next
            items.append(row)
        days.append({"day_number": day_index + 1, "theme": f"Day {day_index + 1}", "items": items})
    return {"days": days}


def _map_title(trip_meta: dict[str, Any]) -> str:
    destination = str(trip_meta.get("destination") or "旅行").strip()
    days = str(trip_meta.get("days") or "").strip()
    return f"{destination}{days}日游地图" if days else f"{destination}旅行地图"


def _line_list_from_payload(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    raw_line_list = payload.get("line_list")
    if isinstance(raw_line_list, list) and raw_line_list:
        return [item for item in raw_line_list if isinstance(item, dict)]

    itinerary_days = _payload_itinerary(overrides).get("days")
    candidates = _payload_candidates(overrides)
    if not isinstance(itinerary_days, list) or not itinerary_days:
        itinerary_days = _rough_days_from_candidates(candidates, _trip_meta_from_payload(payload))

    candidate_by_name = {str(item.get("name") or ""): item for item in candidates if isinstance(item, dict)}
    line_list: list[dict[str, Any]] = []
    for index, day in enumerate(itinerary_days, start=1):
        if not isinstance(day, dict):
            continue
        raw_items = day.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        points: list[dict[str, Any]] = []
        seen_poi_ids: set[str] = set()
        for item in raw_items:
            item_name = item.get("place_name") or item.get("name") or item.get("title") if isinstance(item, dict) else item
            name = str(item_name or "").strip()
            if not name:
                continue
            candidate = candidate_by_name.get(name)
            if not candidate:
                candidate = next(
                    (
                        item
                        for candidate_name, item in candidate_by_name.items()
                        if candidate_name and (candidate_name in name or name in candidate_name)
                    ),
                    None,
                )
            poi = candidate.get("poi") if isinstance(candidate, dict) and isinstance(candidate.get("poi"), dict) else {}
            if not poi and isinstance(item, dict):
                poi = item
            longitude = poi.get("longitude") or poi.get("lon")
            latitude = poi.get("latitude") or poi.get("lat")
            poi_id = poi.get("poi_id") or poi.get("poiId")
            if not poi_id or longitude is None or latitude is None:
                continue
            poi_id = str(poi_id)
            if poi_id in seen_poi_ids:
                continue
            seen_poi_ids.add(poi_id)
            points.append(
                {
                    "name": name,
                    "poiId": poi_id,
                    "lon": longitude,
                    "lat": latitude,
                }
            )
        if points:
            line_list.append(
                {
                    "title": day.get("theme") or day.get("day") or f"Day {index}",
                    "pointInfoList": points[:16],
                }
            )
    return _connect_line_list_days(line_list)


def _connect_line_list_days(line_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index in range(len(line_list) - 1):
        current_points = line_list[index].get("pointInfoList")
        next_points = line_list[index + 1].get("pointInfoList")
        if not isinstance(current_points, list) or not current_points:
            continue
        if not isinstance(next_points, list) or not next_points:
            continue
        current_end = current_points[-1]
        next_start = next_points[0]
        if not isinstance(current_end, dict) or not isinstance(next_start, dict):
            continue
        if current_end.get("poiId") == next_start.get("poiId"):
            continue
        shared = dict(current_end)
        deduped = [shared]
        seen = {str(shared.get("poiId"))}
        for point in next_points:
            if not isinstance(point, dict):
                continue
            poi_id = str(point.get("poiId") or "")
            if poi_id and poi_id in seen:
                continue
            if poi_id:
                seen.add(poi_id)
            deduped.append(point)
        line_list[index + 1]["pointInfoList"] = deduped[:16]
    return line_list


def _trip_meta_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("trip_meta") if isinstance(payload.get("trip_meta"), dict) else {}
    basic = payload.get("basic_info") if isinstance(payload.get("basic_info"), dict) else {}
    return {
        "destination": meta.get("destination") or basic.get("destination") or payload.get("destination"),
        "days": meta.get("days") or basic.get("days") or payload.get("days"),
    }


def _rough_days_from_candidates(candidates: list[dict[str, Any]], trip_meta: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        day_count = int(trip_meta.get("days") or 1)
    except (TypeError, ValueError):
        day_count = 1
    day_count = max(1, min(day_count, 15))
    days: list[dict[str, Any]] = []
    for index in range(day_count):
        items = candidates[index::day_count] or candidates[:1]
        days.append(
            {
                "day_number": index + 1,
                "theme": f"Day {index + 1}",
                "items": [
                    {
                        "place_name": item.get("name"),
                        "name": item.get("name"),
                        **(item.get("poi") if isinstance(item.get("poi"), dict) else {}),
                    }
                    for item in items
                ],
            }
        )
    return days


def _days_from_line_list(line_list: list[Any]) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    for index, line in enumerate(line_list, start=1):
        if not isinstance(line, dict):
            continue
        points = line.get("pointInfoList")
        if not isinstance(points, list):
            points = line.get("points") if isinstance(line.get("points"), list) else []
        items: list[dict[str, Any]] = []
        for order, point in enumerate(points, start=1):
            if not isinstance(point, dict):
                continue
            items.append(
                {
                    "order": order,
                    "place_name": point.get("name"),
                    "poi_id": point.get("poiId") or point.get("poi_id"),
                    "longitude": point.get("lon") or point.get("longitude"),
                    "latitude": point.get("lat") or point.get("latitude"),
                }
            )
        days.append(
            {
                "day_number": index,
                "theme": line.get("title") or f"Day {index}",
                "items": items,
            }
        )
    return days

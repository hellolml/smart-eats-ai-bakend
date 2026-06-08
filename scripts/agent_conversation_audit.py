#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


FALLBACK_MARKERS = ("抱歉，我暂时没能完成这个请求", "fallback")
ENVIRONMENT_FAILURE_CLASSES = {
    "upstream_error",
    "provider_auth",
    "provider_rate_limit",
    "provider_timeout",
    "provider_model_error",
}
NON_QUALITY_FINDING_TYPES = {
    "environment_failure",
    "environment_missing_assistant_response",
    "incomplete_session_without_agent_output",
    "overlapping_user_turn_before_assistant",
}
AFFIRMATIVE_CUES = {"可以", "可以啊", "好", "好的", "行", "行啊", "嗯", "嗯嗯", "继续"}
ROUTE_CUES = ("路线", "导航", "怎么走", "怎么去", "带我去", "前往")
TRAVEL_TOOL_BUDGET = 8
RESTAURANT_REFERENCE_CUES = ("这家", "那家", "刚才", "上面", "推荐", "餐厅", "店")
ORDINAL_RESTAURANT_PATTERNS = (
    ("第一家", 0),
    ("第1家", 0),
    ("第一间", 0),
    ("第1间", 0),
    ("第一個", 0),
    ("第1个", 0),
    ("第一個", 0),
    ("选第一", 0),
    ("就第一", 0),
    ("第二家", 1),
    ("第2家", 1),
    ("第二间", 1),
    ("第2间", 1),
    ("第二个", 1),
    ("第2个", 1),
    ("选第二", 1),
    ("就第二", 1),
    ("第三家", 2),
    ("第3家", 2),
    ("第三间", 2),
    ("第3间", 2),
    ("第三个", 2),
    ("第3个", 2),
)


@dataclass
class Message:
    session_id: str
    scene: str
    title: str
    role: str
    content: str
    tool_name: str | None
    tool_payload_json: str | None
    created_at: str


@dataclass
class Turn:
    session_id: str
    scene: str
    title: str
    user_message: str
    assistant_message: str = ""
    assistant_payload: dict[str, Any] | None = None
    tools: list[str] = field(default_factory=list)
    tool_payloads: list[dict[str, Any]] = field(default_factory=list)
    interrupted_by_next_user: bool = False


def load_messages(
    db_path: Path,
    *,
    limit_sessions: int,
    session_ids: list[str] | None = None,
) -> list[Message]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        selected_session_ids = [item for item in (session_ids or []) if item]
        if not selected_session_ids:
            session_rows = conn.execute(
                """
                select cs.id
                from chat_sessions cs
                join chat_messages cm on cm.session_id = cs.id
                where cs.deleted_at is null
                group by cs.id
                order by max(cm.created_at) desc
                limit ?
                """,
                (limit_sessions,),
            ).fetchall()
            selected_session_ids = [str(row["id"]) for row in session_rows]
        if not selected_session_ids:
            return []
        placeholders = ",".join("?" for _ in selected_session_ids)
        rows = conn.execute(
            f"""
            select cm.session_id, cs.scene, coalesce(cs.title, '') as title,
                   cm.role, coalesce(cm.content, '') as content,
                   cm.tool_name, cm.tool_payload_json, cm.created_at
            from chat_messages cm
            join chat_sessions cs on cs.id = cm.session_id
            where cm.session_id in ({placeholders})
            order by cm.session_id, cm.created_at, cm.id
            """,
            selected_session_ids,
        ).fetchall()
    return [
        Message(
            session_id=str(row["session_id"]),
            scene=str(row["scene"] or ""),
            title=str(row["title"] or ""),
            role=str(row["role"] or ""),
            content=str(row["content"] or ""),
            tool_name=str(row["tool_name"]) if row["tool_name"] else None,
            tool_payload_json=str(row["tool_payload_json"]) if row["tool_payload_json"] else None,
            created_at=str(row["created_at"] or ""),
        )
        for row in rows
    ]


def build_turns(messages: list[Message]) -> list[Turn]:
    turns: list[Turn] = []
    current: Turn | None = None
    for message in messages:
        if message.role == "user":
            if current is not None:
                current.interrupted_by_next_user = True
                turns.append(current)
            current = Turn(
                session_id=message.session_id,
                scene=message.scene,
                title=message.title,
                user_message=message.content,
            )
            continue
        if current is None:
            continue
        if message.role == "tool":
            if message.tool_name:
                current.tools.append(message.tool_name)
            payload = parse_json(message.tool_payload_json)
            if isinstance(payload, dict):
                current.tool_payloads.append(payload)
            continue
        if message.role == "assistant":
            current.assistant_message = message.content
            payload = parse_json(message.tool_payload_json)
            if isinstance(payload, dict):
                current.assistant_payload = payload
            turns.append(current)
            current = None
    if current is not None:
        turns.append(current)
    return turns


def parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def audit_turns(turns: list[Turn]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    previous_assistant_by_session: dict[str, str] = {}
    recent_restaurants_by_session: dict[str, list[str]] = {}
    tool_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    worker_counts: dict[str, int] = {}
    failure_class_counts: dict[str, int] = {}
    provider_issue_counts: dict[str, int] = {}
    provider_issue_category_counts: dict[str, int] = {}
    provider_action_counts: dict[str, int] = {}
    fallback_count = 0
    environment_failure_count = 0
    expected_travel_days_by_session: dict[str, int] = {}
    environment_failed_sessions: set[str] = set()
    sessions_with_seen_agent_output: set[str] = set()

    for turn in turns:
        for tool in turn.tools:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1

        status = extract_status(turn)
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        worker = extract_worker(turn)
        if worker:
            worker_counts[worker] = worker_counts.get(worker, 0) + 1
        failure_class = extract_failure_class(turn)
        if failure_class:
            failure_class_counts[failure_class] = failure_class_counts.get(failure_class, 0) + 1
        provider_issue = extract_provider_issue(turn)
        if provider_issue:
            issue_code = provider_issue.get("code")
            if isinstance(issue_code, str) and issue_code:
                provider_issue_counts[issue_code] = provider_issue_counts.get(issue_code, 0) + 1
            category = provider_issue.get("category")
            if isinstance(category, str) and category:
                provider_issue_category_counts[category] = provider_issue_category_counts.get(category, 0) + 1
            action = provider_issue.get("action")
            if isinstance(action, str) and action:
                provider_action_counts[action] = provider_action_counts.get(action, 0) + 1
        is_environment_failure = failure_class in ENVIRONMENT_FAILURE_CLASSES
        fallback = any(marker in turn.assistant_message for marker in FALLBACK_MARKERS)
        if is_environment_failure:
            environment_failure_count += 1
            environment_failed_sessions.add(turn.session_id)
            findings.append(
                finding(
                    "environment_failure",
                    turn,
                    f"assistant failed due to environment/provider failure_class={failure_class}",
                    severity="info",
                    failure_class=failure_class,
                )
            )
        if fallback and not is_environment_failure:
            fallback_count += 1
            findings.append(finding("fallback", turn, "assistant returned fallback text"))
            if not turn.tools:
                findings.append(
                    finding(
                        "no_tool_fallback",
                        turn,
                        "assistant returned fallback without any recorded tool calls",
                        severity="high",
                    )
                )

        if not turn.assistant_message and turn.assistant_payload is None:
            if turn.session_id in environment_failed_sessions:
                findings.append(
                    finding(
                        "environment_missing_assistant_response",
                        turn,
                        "user turn has no assistant response after an earlier environment/provider failure in the same session",
                        severity="info",
                    )
                )
            elif turn.interrupted_by_next_user:
                findings.append(
                    finding(
                        "overlapping_user_turn_before_assistant",
                        turn,
                        "another user message arrived before an assistant response was recorded",
                        severity="info",
                    )
                )
            elif turn.session_id not in sessions_with_seen_agent_output and not turn.tools and not turn.tool_payloads:
                findings.append(
                    finding(
                        "incomplete_session_without_agent_output",
                        turn,
                        "session has user turns but no recorded assistant or tool output",
                        severity="info",
                    )
                )
            else:
                findings.append(
                    finding(
                        "missing_assistant_response",
                        turn,
                        "user turn has no assistant response",
                        severity="medium",
                    )
                )

        expected_days = extract_requested_travel_days(turn.user_message)
        if expected_days:
            expected_travel_days_by_session[turn.session_id] = expected_days

        travel_poi_count = turn.tools.count("travel_search_poi") + turn.tools.count("travel_search_nearby_poi")
        if travel_poi_count > TRAVEL_TOOL_BUDGET:
            findings.append(
                finding(
                    "travel_tool_explosion",
                    turn,
                    f"travel poi tool calls {travel_poi_count} > budget {TRAVEL_TOOL_BUDGET}",
                    severity="high",
                )
            )

        if any(cue in turn.user_message for cue in ROUTE_CUES) and "memory_search" in turn.tools:
            findings.append(
                finding(
                    "route_memory_tool_leak",
                    turn,
                    "route-like turn called memory_search",
                    severity="medium",
                )
            )

        previous_assistant = previous_assistant_by_session.get(turn.session_id, "")
        cleaned_user = turn.user_message.strip().strip("，。！？!?,. ")
        if (
            cleaned_user in AFFIRMATIVE_CUES
            and ("帮你筛一轮" in previous_assistant or "按距离、评分或口味" in previous_assistant)
            and "food_decision" in turn.tools
            and "search_restaurants" not in turn.tools
        ):
            findings.append(
                finding(
                    "food_affirmation_mode_drift",
                    turn,
                    "affirmative restaurant follow-up used food_decision without restaurant search",
                    severity="high",
                )
            )

        restaurants = extract_restaurant_names(turn)
        selected_restaurant = selected_recent_restaurant(
            turn.user_message,
            [*recent_restaurants_by_session.get(turn.session_id, []), *restaurants],
        )
        references_recent_restaurant_route = route_to_recent_restaurant(
            turn.user_message,
            recent_restaurants_by_session.get(turn.session_id, []),
        )
        if selected_restaurant:
            worker = extract_worker(turn)
            if worker == "general_chat" or "memory_search" in turn.tools or "source_event_search" in turn.tools:
                findings.append(
                    finding(
                        "restaurant_selection_context_loss",
                        turn,
                        f"user selected recent restaurant {selected_restaurant}, but turn routed to {worker or 'unknown'} with non-food tools",
                        severity="high",
                    )
                )
            elif "food_decision" in turn.tools:
                findings.append(
                    finding(
                        "restaurant_selection_ack",
                        turn,
                        f"user selected recent restaurant {selected_restaurant}, but turn used food_decision",
                        severity="high",
                    )
                )
            elif selected_restaurant not in turn.assistant_message:
                findings.append(
                    finding(
                        "restaurant_selection_ack",
                        turn,
                        f"user selected recent restaurant {selected_restaurant}, but assistant did not confirm it",
                        severity="medium",
                    )
                )
        if references_recent_restaurant_route:
            worker = extract_worker(turn)
            unexpected_tools = sorted({"food_decision", "search_restaurants", "memory_search", "source_event_search"} & set(turn.tools))
            if worker != "route_planner" or "plan_route" not in turn.tools or unexpected_tools:
                findings.append(
                    finding(
                        "restaurant_route_context_loss",
                        turn,
                        (
                            f"user asked route to recent restaurant {references_recent_restaurant_route}, "
                            f"but worker={worker or 'unknown'}, plan_route={'plan_route' in turn.tools}, "
                            f"unexpected_tools={unexpected_tools}"
                        ),
                        severity="high",
                    )
                )

        travel_final = extract_travel_final(turn)
        if travel_final:
            trip_meta = travel_final.get("trip_meta") if isinstance(travel_final.get("trip_meta"), dict) else {}
            if has_structured_travel_request(turn.user_message) and (
                not trip_meta.get("destination") or not trip_meta.get("days")
            ):
                findings.append(
                    finding(
                        "travel_trip_meta_missing",
                        turn,
                        "structured travel request did not preserve destination/days in trip_meta",
                        severity="high",
                    )
                )
            bad_places = prompt_artifact_place_names(travel_final)
            if bad_places:
                findings.append(
                    finding(
                        "travel_prompt_text_extracted_as_poi",
                        turn,
                        f"assistant treated prompt/helper text as POI: {', '.join(bad_places[:3])}",
                        severity="high",
                    )
                )
            itinerary_days = itinerary_day_count(travel_final)
            expected_days = expected_travel_days_by_session.get(turn.session_id)
            if expected_days and travel_final.get("state") in {"itinerary_generated", "map_generated"} and itinerary_days and itinerary_days < expected_days:
                findings.append(
                    finding(
                        "travel_itinerary_day_mismatch",
                        turn,
                        f"expected {expected_days} travel days but final itinerary has {itinerary_days}",
                        severity="high",
                    )
                )

        if turn.assistant_message:
            previous_assistant_by_session[turn.session_id] = turn.assistant_message
        if turn.assistant_message or turn.assistant_payload is not None or turn.tools or turn.tool_payloads:
            sessions_with_seen_agent_output.add(turn.session_id)
        if restaurants:
            existing = recent_restaurants_by_session.setdefault(turn.session_id, [])
            for name in restaurants:
                if name not in existing:
                    existing.append(name)
            recent_restaurants_by_session[turn.session_id] = existing[-24:]

    return {
        "total_turns": len(turns),
        "fallback_count": fallback_count,
        "fallback_rate": fallback_count / len(turns) if turns else 0.0,
        "environment_failure_count": environment_failure_count,
        "environment_failure_rate": environment_failure_count / len(turns) if turns else 0.0,
        "tool_counts": dict(sorted(tool_counts.items(), key=lambda item: item[1], reverse=True)),
        "status_counts": dict(sorted(status_counts.items(), key=lambda item: item[1], reverse=True)),
        "worker_counts": dict(sorted(worker_counts.items(), key=lambda item: item[1], reverse=True)),
        "failure_class_counts": dict(sorted(failure_class_counts.items(), key=lambda item: item[1], reverse=True)),
        "provider_issue_counts": dict(sorted(provider_issue_counts.items(), key=lambda item: item[1], reverse=True)),
        "provider_issue_category_counts": dict(sorted(provider_issue_category_counts.items(), key=lambda item: item[1], reverse=True)),
        "provider_action_counts": dict(sorted(provider_action_counts.items(), key=lambda item: item[1], reverse=True)),
        "finding_count": len(findings),
        "quality_finding_count": sum(1 for item in findings if item.get("type") not in NON_QUALITY_FINDING_TYPES),
        "findings_by_type": summarize_findings(findings),
        "findings": findings,
    }


def extract_status(turn: Turn) -> str | None:
    for payload in [turn.assistant_payload, *turn.tool_payloads]:
        if not isinstance(payload, dict):
            continue
        direct = payload.get("status")
        if isinstance(direct, str) and direct:
            return direct
        answer = payload.get("answer")
        if isinstance(answer, dict):
            answer_status = answer.get("status")
            if isinstance(answer_status, str) and answer_status:
                return answer_status
        agent_result = payload.get("agent_result")
        if isinstance(agent_result, dict):
            status = agent_result.get("status")
            if isinstance(status, str) and status:
                return status
    return None


def extract_worker(turn: Turn) -> str | None:
    for payload in [turn.assistant_payload, *turn.tool_payloads]:
        if not isinstance(payload, dict):
            continue
        agent_result = payload.get("agent_result")
        if not isinstance(agent_result, dict):
            continue
        worker = agent_result.get("worker")
        if isinstance(worker, str) and worker:
            return worker
        diagnostics = agent_result.get("diagnostics")
        route = diagnostics.get("route") if isinstance(diagnostics, dict) else None
        routed_worker = route.get("worker") if isinstance(route, dict) else None
        if isinstance(routed_worker, str) and routed_worker:
            return routed_worker
    return None


def extract_provider_issue(turn: Turn) -> dict[str, Any] | None:
    for payload in [turn.assistant_payload, *turn.tool_payloads]:
        if not isinstance(payload, dict):
            continue
        direct = payload.get("provider_issue")
        if isinstance(direct, dict):
            return direct
        answer = payload.get("answer")
        if isinstance(answer, dict) and isinstance(answer.get("provider_issue"), dict):
            return answer["provider_issue"]
        agent_result = payload.get("agent_result")
        if not isinstance(agent_result, dict):
            continue
        final = agent_result.get("final")
        if isinstance(final, dict) and isinstance(final.get("provider_issue"), dict):
            return final["provider_issue"]
        diagnostics = agent_result.get("diagnostics")
        if isinstance(diagnostics, dict) and isinstance(diagnostics.get("provider_issue"), dict):
            return diagnostics["provider_issue"]
    return None


def extract_failure_class(turn: Turn) -> str | None:
    payloads = [turn.assistant_payload, *turn.tool_payloads]
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        direct = payload.get("failure_class")
        if isinstance(direct, str) and direct:
            return direct
        answer = payload.get("answer")
        if isinstance(answer, dict):
            answer_failure = answer.get("failure_class")
            if isinstance(answer_failure, str) and answer_failure:
                return answer_failure
        agent_result = payload.get("agent_result")
        if isinstance(agent_result, dict):
            agent_failure = agent_result.get("failure_class")
            if isinstance(agent_failure, str) and agent_failure:
                return agent_failure
            final = agent_result.get("final")
            if isinstance(final, dict):
                final_failure = final.get("failure_class")
                if isinstance(final_failure, str) and final_failure:
                    return final_failure
    return None


def extract_restaurant_names(turn: Turn) -> list[str]:
    names: list[str] = []
    for payload in [turn.assistant_payload, *turn.tool_payloads]:
        for name in _restaurant_names_from_payload(payload):
            if name not in names:
                names.append(name)
    return names


def _restaurant_names_from_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    names: list[str] = []
    answer = payload.get("answer")
    if isinstance(answer, dict):
        names.extend(_restaurant_names_from_recommendations(answer.get("recommendations")))
    result = payload.get("result")
    if isinstance(result, list):
        names.extend(_restaurant_names_from_restaurant_rows(result))
    result_preview = payload.get("result_preview")
    if isinstance(result_preview, list):
        names.extend(_restaurant_names_from_restaurant_rows(result_preview))
    if payload.get("tool_name") == "search_restaurants":
        names.extend(_restaurant_names_from_restaurant_rows(payload.get("result")))
    return [name for index, name in enumerate(names) if name and name not in names[:index]]


def _restaurant_names_from_recommendations(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict) or item.get("type") != "restaurant":
            continue
        title = str(item.get("title") or "").strip()
        if title:
            names.append(title)
        raw = item.get("raw")
        if isinstance(raw, dict):
            raw_name = str(raw.get("name") or "").strip()
            if raw_name:
                names.append(raw_name)
    return names


def _restaurant_names_from_restaurant_rows(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        if name:
            names.append(name)
    return names


def selected_recent_restaurant(message: Any, restaurant_names: list[str]) -> str | None:
    text = normalize_selection_text(str(message or ""))
    if not text:
        return None
    for name in reversed(restaurant_names):
        aliases = restaurant_aliases(name)
        if any(alias and alias in text for alias in aliases):
            return name
    ordinal = restaurant_ordinal_index(str(message or ""))
    if ordinal is not None and 0 <= ordinal < len(restaurant_names):
        return restaurant_names[ordinal]
    return None


def route_to_recent_restaurant(message: Any, restaurant_names: list[str]) -> str | None:
    text = str(message or "")
    if not restaurant_names or not any(cue in text for cue in ROUTE_CUES):
        return None
    selected = selected_recent_restaurant(text, restaurant_names)
    if selected:
        return selected
    if any(cue in text for cue in RESTAURANT_REFERENCE_CUES):
        return restaurant_names[-1]
    return None


def restaurant_ordinal_index(message: str) -> int | None:
    compact = normalize_selection_text(message)
    for pattern, index in ORDINAL_RESTAURANT_PATTERNS:
        if normalize_selection_text(pattern) in compact:
            return index
    return None


def restaurant_aliases(name: str) -> list[str]:
    aliases: list[str] = []
    for value in (name, name.split("(", 1)[0], name.split("（", 1)[0]):
        cleaned = normalize_selection_text(value)
        if len(cleaned) >= 2 and cleaned not in aliases:
            aliases.append(cleaned)
    return aliases


def normalize_selection_text(value: str) -> str:
    text = str(value or "").strip().lower()
    for token in (" ", "\t", "\n", "，", "。", "！", "？", "!", "?", ",", ".", "就", "选", "去"):
        text = text.replace(token, "")
    while text.endswith(("吧", "把", "呗", "呢", "啦", "了")):
        text = text[:-1]
    return text


def extract_travel_final(turn: Turn) -> dict[str, Any] | None:
    for payload in [turn.assistant_payload, *turn.tool_payloads]:
        if not isinstance(payload, dict):
            continue
        answer = payload.get("answer")
        if isinstance(answer, dict) and _looks_like_travel_final(answer):
            return answer
        agent_result = payload.get("agent_result")
        final = agent_result.get("final") if isinstance(agent_result, dict) else None
        if isinstance(final, dict) and _looks_like_travel_final(final):
            return final
    return None


def _looks_like_travel_final(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("trip_meta", "candidates", "itinerary", "map")) and (
        value.get("state") is not None or value.get("trip_meta") is not None
    )


def has_structured_travel_request(message: str) -> bool:
    text = str(message or "")
    return "目的地" in text and any(token in text for token in ("出行天数", "旅行天数", "游玩天数"))


def extract_requested_travel_days(message: str) -> int | None:
    text = str(message or "")
    match = re_search(r"(?:出行天数|旅行天数|游玩天数|天数)\s*[:：]\s*([^\n\r]+)", text)
    value = match.group(1) if match else text
    digit = re_search(r"(\d+)\s*天", value)
    if digit:
        return int(digit.group(1))
    chinese = re_search(r"([一二两三四五六七八九十]+)\s*天", value)
    if chinese:
        return chinese_number(chinese.group(1))
    return None


def re_search(pattern: str, value: str) -> Any:
    import re

    return re.search(pattern, value)


def chinese_number(value: str) -> int | None:
    numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    text = str(value or "")
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2:
        return 10 + numerals.get(text[1], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return numerals.get(left, 1) * 10 + numerals.get(right, 0)
    for char in text:
        if char in numerals:
            return numerals[char]
    return None


def prompt_artifact_place_names(final: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("places", "candidates"):
        value = final.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or item.get("source_name") or "").strip()
            if is_prompt_artifact_place(name) and name not in names:
                names.append(name)
    return names


def is_prompt_artifact_place(name: str) -> bool:
    text = str(name or "")
    if not text:
        return False
    return len(text) > 20 and any(
        token in text
        for token in (
            "我可以",
            "您可以",
            "你可以",
            "有什么特别想去",
            "请补充",
            "上传攻略截图",
            "高德验证POI",
        )
    )


def itinerary_day_count(final: dict[str, Any]) -> int:
    itinerary = final.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, dict) else None
    return len(days) if isinstance(days, list) else 0


def finding(
    kind: str,
    turn: Turn,
    reason: str,
    *,
    severity: str = "medium",
    failure_class: str | None = None,
) -> dict[str, Any]:
    item = {
        "type": kind,
        "severity": severity,
        "session_id": turn.session_id,
        "scene": turn.scene,
        "title": turn.title,
        "user_message": turn.user_message,
        "tools": turn.tools,
        "reason": reason,
    }
    if failure_class:
        item["failure_class"] = failure_class
    return item


def summarize_findings(findings: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in findings:
        kind = str(item.get("type") or "unknown")
        summary[kind] = summary.get(kind, 0) + 1
    return dict(sorted(summary.items(), key=lambda item: item[1], reverse=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit local Smart Eats chat conversations for agent quality issues")
    parser.add_argument("--db", default="local.db", help="SQLite database path")
    parser.add_argument("--limit-sessions", type=int, default=50, help="Recent sessions to inspect")
    parser.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="Specific session id to inspect; can be repeated. Overrides recent-session selection.",
    )
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    parser.add_argument("--fail-on-findings", action="store_true", help="Exit non-zero when findings are present")
    parser.add_argument(
        "--fail-on-quality-findings",
        action="store_true",
        help="Exit non-zero when product-quality findings are present; environment failures do not fail this gate.",
    )
    args = parser.parse_args()

    session_ids = [str(item).strip() for item in args.session_id if str(item).strip()]
    report = audit_turns(
        build_turns(
            load_messages(
                Path(args.db),
                limit_sessions=args.limit_sessions,
                session_ids=session_ids or None,
            )
        )
    )
    if session_ids:
        report["session_ids"] = session_ids
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    if args.fail_on_findings and report["finding_count"]:
        raise SystemExit(1)
    if args.fail_on_quality_findings and report["quality_finding_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

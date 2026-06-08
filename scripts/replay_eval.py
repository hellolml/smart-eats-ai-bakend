#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


WORKER_ALLOWED_TOOLS: dict[str, set[str]] = {
    "food_advisor": {
        "food_decision",
        "get_ip_location",
        "geocode_location",
        "search_restaurants",
        "get_weather",
    },
    "home_chef": {
        "get_fridge_items",
        "rag_search_recipes",
        "search_recipes",
    },
    "route_planner": {
        "geocode_location",
        "plan_route",
    },
    "general_chat": {
        "memory_search",
        "memory_write",
        "memory_update",
        "memory_forget",
        "source_event_search",
    },
    "travel_planner": {
        "travel_fetch_url_content",
        "travel_search_poi",
        "travel_search_nearby_poi",
        "travel_create_personal_map",
    },
}

BAD_VISIBLE_PHRASES = (
    "人均 人均",
    "旅行旅行",
    "地图地图",
)
BAD_PROMPT_ARTIFACT_POI_PHRASES = (
    "这样我可以",
    "您有什么特别想去",
    "高德验证POI",
    "请补充",
    "上传攻略截图",
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_fallback(answer: dict[str, Any]) -> bool:
    recs = answer.get("recommendations") if isinstance(answer, dict) else None
    if not isinstance(recs, list):
        return False
    for item in recs:
        if isinstance(item, dict) and str(item.get("reason") or "") == "fallback":
            return True
    return False


def final_event_from_sse(text: str) -> dict[str, Any] | None:
    final_payload: dict[str, Any] | None = None
    event = None
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event == "final":
            data = line.split(":", 1)[1].strip()
            try:
                final_payload = json.loads(data)
            except json.JSONDecodeError:
                pass
    return final_payload


def evaluate_result(
    case: dict[str, Any],
    result: dict[str, Any],
    *,
    allowed_environment_failures: set[str] | None = None,
) -> dict[str, Any]:
    expect = case.get("expect")
    if not isinstance(expect, dict):
        return {"passed": True, "violations": []}

    failure_class = result.get("failure_class")
    is_allowed_environment_failure = (
        isinstance(failure_class, str)
        and allowed_environment_failures is not None
        and failure_class in allowed_environment_failures
    )
    if result.get("harness_environment_failure") and is_allowed_environment_failure:
        return {"passed": True, "violations": []}

    violations: list[str] = []
    violations.extend(validate_final_contract(result))
    violations.extend(validate_worker_tool_boundary(result))
    violations.extend(validate_visible_text_quality(result))
    if expect.get("no_fallback") is True and result.get("fallback") and not is_allowed_environment_failure:
        violations.append(f"unexpected_fallback:{result.get('failure_class') or 'legacy'}")

    if expect.get("no_tool_calls") is True:
        tool_calls = result.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            violations.append(f"unexpected_tool_calls:{','.join(str(item) for item in tool_calls)}")

    tool_calls = result.get("tool_calls")
    tool_call_set = {item for item in tool_calls if isinstance(item, str)} if isinstance(tool_calls, list) else set()
    tool_calls_include = expect.get("tool_calls_include")
    if isinstance(tool_calls_include, list):
        missing = sorted({item for item in tool_calls_include if isinstance(item, str) and item not in tool_call_set})
        if missing:
            violations.append(f"missing_tool_calls:{','.join(missing)}")

    tool_calls_exclude = expect.get("tool_calls_exclude")
    if isinstance(tool_calls_exclude, list):
        unexpected = sorted({item for item in tool_calls_exclude if isinstance(item, str) and item in tool_call_set})
        if unexpected:
            violations.append(f"unexpected_tool_calls:{','.join(unexpected)}")

    expected_worker = expect.get("worker")
    if isinstance(expected_worker, str) and result.get("worker") != expected_worker:
        violations.append(f"worker:{result.get('worker')}!=expected:{expected_worker}")

    worker_in = expect.get("worker_in")
    if isinstance(worker_in, list) and result.get("worker") not in set(worker_in):
        violations.append(f"worker:{result.get('worker')} not in {worker_in}")

    intent_in = expect.get("intent_in")
    if isinstance(intent_in, list):
        actual_intent = result.get("intent") or "unknown"
        if actual_intent not in set(intent_in):
            violations.append(f"intent:{actual_intent} not in {intent_in}")

    status_in = expect.get("status_in")
    if isinstance(status_in, list):
        actual_status = result.get("status") or "unknown"
        if actual_status not in set(status_in):
            violations.append(f"status:{actual_status} not in {status_in}")

    recommendation_titles_include = expect.get("recommendation_titles_include")
    if isinstance(recommendation_titles_include, list):
        titles = _recommendation_titles(result)
        missing_titles = [
            item
            for item in recommendation_titles_include
            if isinstance(item, str) and item and not any(item in title for title in titles)
        ]
        if missing_titles:
            violations.append(f"missing_recommendation_titles:{','.join(missing_titles)}")

    trip_meta_expect = expect.get("trip_meta")
    if isinstance(trip_meta_expect, dict):
        trip_meta = _travel_trip_meta(result)
        for key, value in trip_meta_expect.items():
            if trip_meta.get(key) != value:
                violations.append(f"trip_meta:{key}:{trip_meta.get(key)}!=expected:{value}")

    min_itinerary_days = expect.get("min_itinerary_days")
    if isinstance(min_itinerary_days, int):
        day_count = _itinerary_day_count(result)
        if day_count < min_itinerary_days:
            violations.append(f"itinerary_days:{day_count}<{min_itinerary_days}")

    if expect.get("no_prompt_artifact_pois") is True:
        bad_names = _prompt_artifact_poi_names(result)
        if bad_names:
            violations.append(f"prompt_artifact_pois:{','.join(bad_names[:3])}")

    candidate_expected_any = expect.get("candidate_expected_any")
    if isinstance(candidate_expected_any, list):
        names = _travel_active_place_names(result)
        missing_any = [
            item
            for item in candidate_expected_any
            if isinstance(item, str) and item and not any(item in name for name in names)
        ]
        if missing_any:
            violations.append(f"candidate_missing_any:{','.join(missing_any)}")

    candidate_unexpected_any = expect.get("candidate_unexpected_any")
    if isinstance(candidate_unexpected_any, list):
        names = _travel_active_place_names(result)
        unexpected = [
            item
            for item in candidate_unexpected_any
            if isinstance(item, str) and item and any(item in name for name in names)
        ]
        if unexpected:
            violations.append(f"candidate_unexpected_any:{','.join(unexpected)}")

    return {"passed": not violations, "violations": violations}


def validate_final_contract(result: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    agent_result = result.get("agent_result")
    if not isinstance(agent_result, dict) or not agent_result:
        return ["contract:missing_agent_result"]

    status = agent_result.get("status")
    if status not in {"completed", "needs_clarification", "failed", "blocked"}:
        violations.append(f"contract:invalid_status:{status}")

    trace_id = result.get("trace_id") or agent_result.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        violations.append("contract:missing_trace_id")

    worker = result.get("worker") or agent_result.get("worker")
    if not isinstance(worker, str) or not worker:
        violations.append("contract:missing_worker")

    diagnostics = agent_result.get("diagnostics")
    route = diagnostics.get("route") if isinstance(diagnostics, dict) else None
    routed_worker = route.get("worker") if isinstance(route, dict) else None
    result_worker = agent_result.get("worker")
    if isinstance(routed_worker, str) and routed_worker:
        if result.get("worker") and result.get("worker") != routed_worker:
            violations.append(f"contract:result_worker_route_mismatch:{result.get('worker')}!={routed_worker}")
        if isinstance(result_worker, str) and result_worker and result_worker != routed_worker:
            violations.append(f"contract:agent_result_worker_route_mismatch:{result_worker}!={routed_worker}")

    final = agent_result.get("final")
    if not isinstance(final, dict):
        violations.append("contract:missing_final")

    failure_class = result.get("failure_class") or agent_result.get("failure_class")
    if status == "failed" and not failure_class:
        violations.append("contract:failed_missing_failure_class")
    if status != "failed" and failure_class:
        violations.append(f"contract:non_failed_has_failure_class:{failure_class}")
    return violations


def validate_worker_tool_boundary(result: dict[str, Any]) -> list[str]:
    worker = result.get("worker")
    if not isinstance(worker, str):
        return []
    allowed = WORKER_ALLOWED_TOOLS.get(worker)
    if allowed is None:
        return []
    tools = result.get("active_tools")
    if not isinstance(tools, list):
        return []
    leaked = sorted({tool for tool in tools if isinstance(tool, str) and tool not in allowed})
    if not leaked:
        return []
    return [f"tool_boundary:{worker}:unexpected:{','.join(leaked)}"]


def validate_visible_text_quality(result: dict[str, Any]) -> list[str]:
    visible_payloads = []
    answer = result.get("answer")
    if isinstance(answer, dict):
        visible_payloads.append(answer)
    agent_result = result.get("agent_result")
    final = agent_result.get("final") if isinstance(agent_result, dict) else None
    if isinstance(final, dict):
        visible_payloads.append(final)

    violations: list[str] = []
    seen: set[str] = set()
    for text in _iter_strings(visible_payloads):
        for phrase in BAD_VISIBLE_PHRASES:
            if phrase in text and phrase not in seen:
                violations.append(f"visible_text:duplicated_phrase:{phrase}")
                seen.add(phrase)
    return violations


def _recommendation_titles(result: dict[str, Any]) -> list[str]:
    agent_result = result.get("agent_result")
    final = agent_result.get("final") if isinstance(agent_result, dict) else result.get("answer")
    if not isinstance(final, dict):
        return []
    recommendations = final.get("recommendations")
    if not isinstance(recommendations, list):
        return []
    titles: list[str] = []
    for item in recommendations:
        if isinstance(item, dict) and isinstance(item.get("title"), str):
            titles.append(item["title"])
    return titles


def _final_payload(result: dict[str, Any]) -> dict[str, Any]:
    agent_result = result.get("agent_result")
    if isinstance(agent_result, dict) and isinstance(agent_result.get("final"), dict):
        return agent_result["final"]
    answer = result.get("answer")
    return answer if isinstance(answer, dict) else {}


def _travel_trip_meta(result: dict[str, Any]) -> dict[str, Any]:
    final = _final_payload(result)
    trip_meta = final.get("trip_meta")
    return trip_meta if isinstance(trip_meta, dict) else {}


def _itinerary_day_count(result: dict[str, Any]) -> int:
    final = _final_payload(result)
    itinerary = final.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, dict) else None
    return len(days) if isinstance(days, list) else 0


def _prompt_artifact_poi_names(result: dict[str, Any]) -> list[str]:
    final = _final_payload(result)
    names: list[str] = []

    def add_name(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        if any(phrase in value for phrase in BAD_PROMPT_ARTIFACT_POI_PHRASES):
            names.append(value.strip())

    for key in ("places", "candidates"):
        items = final.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                add_name(item.get("name") or item.get("source_name") or item.get("verified_name"))

    itinerary = final.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, dict) else None
    if isinstance(days, list):
        for day in days:
            items = day.get("items") if isinstance(day, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    add_name(item.get("place_name") or item.get("name"))

    return list(dict.fromkeys(names))


def _travel_active_place_names(result: dict[str, Any]) -> list[str]:
    final = _final_payload(result)
    names: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            names.append(value.strip())

    for key in ("places", "candidates", "failed_places"):
        items = final.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                add(item.get("name") or item.get("source_name") or item.get("verified_name"))

    itinerary = final.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, dict) else None
    if isinstance(days, list):
        for day in days:
            items = day.get("items") if isinstance(day, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    add(item.get("place_name") or item.get("name"))

    return list(dict.fromkeys(names))


def run_case(
    base_url: str,
    case: dict[str, Any],
    *,
    model_value: str | None = None,
    allowed_environment_failures: set[str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    import httpx

    with httpx.Client(base_url=base_url, timeout=timeout_seconds) as client:
        try:
            r = client.post("/api/v1/chat/sessions")
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return _http_error_result(
                case,
                exc.response,
                model_value=model_value,
                allowed_environment_failures=allowed_environment_failures,
            )
        except httpx.TimeoutException as exc:
            return _request_error_result(
                case,
                "timeout",
                str(exc),
                model_value=model_value,
                allowed_environment_failures=allowed_environment_failures,
            )
        except httpx.RequestError as exc:
            return _request_error_result(
                case,
                "request_error",
                str(exc),
                model_value=model_value,
                allowed_environment_failures=allowed_environment_failures,
            )
        session_id = r.json()["data"]["session_id"]

        turns = case.get("turns")
        if isinstance(turns, list):
            turn_results = [
                run_turn(
                    client,
                    session_id,
                    {**turn, "id": f"{case.get('id')}:{index + 1}"},
                    model_value=model_value,
                    allowed_environment_failures=allowed_environment_failures,
                    timeout_seconds=timeout_seconds,
                )
                for index, turn in enumerate(turns)
                if isinstance(turn, dict)
            ]
            return {
                "id": case.get("id"),
                "message": None,
                "fallback": any(item.get("fallback") for item in turn_results),
                "status": turn_results[-1].get("status") if turn_results else None,
                "worker": turn_results[-1].get("worker") if turn_results else None,
                "intent": turn_results[-1].get("intent") if turn_results else None,
                "failure_class": next((item.get("failure_class") for item in turn_results if item.get("failure_class")), None),
                "turns": turn_results,
                "evaluation": {
                    "passed": all((item.get("evaluation") or {}).get("passed") for item in turn_results),
                    "violations": [
                        f"{item.get('id')}:{violation}"
                        for item in turn_results
                        for violation in ((item.get("evaluation") or {}).get("violations") or [])
                    ],
                },
            }

        return run_turn(
            client,
            session_id,
            case,
            model_value=model_value,
            allowed_environment_failures=allowed_environment_failures,
            timeout_seconds=timeout_seconds,
        )


def run_turn(
    client: Any,
    session_id: str,
    case: dict[str, Any],
    *,
    model_value: str | None = None,
    allowed_environment_failures: set[str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    import httpx

    try:
        resp = client.post(
            f"/api/v1/chat/sessions/{session_id}/stream",
            json={
                key: value
                for key, value in {
                    "message": case["message"],
                    "scene": case.get("scene"),
                    "model": model_value,
                    "client_context_overrides": case.get("client_context_overrides")
                    or case.get("context_overrides"),
                }.items()
                if value is not None
            },
            headers={"accept": "text/event-stream"},
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _http_error_result(case, exc.response, model_value=model_value, allowed_environment_failures=allowed_environment_failures)
    except httpx.TimeoutException as exc:
        return _request_error_result(case, "timeout", str(exc), model_value=model_value, allowed_environment_failures=allowed_environment_failures)
    except httpx.RequestError as exc:
        return _request_error_result(case, "request_error", str(exc), model_value=model_value, allowed_environment_failures=allowed_environment_failures)

    final_payload = final_event_from_sse(resp.text)

    answer = ((final_payload or {}).get("answer") or {}) if isinstance(final_payload, dict) else {}
    agent_result = ((final_payload or {}).get("agent_result") or {}) if isinstance(final_payload, dict) else {}
    diagnostics = agent_result.get("diagnostics") if isinstance(agent_result, dict) else {}
    route = diagnostics.get("route") if isinstance(diagnostics, dict) else {}
    failure_class = (final_payload or {}).get("failure_class") if isinstance(final_payload, dict) else None
    status = agent_result.get("status") if isinstance(agent_result, dict) else None
    result = {
        "id": case.get("id"),
        "message": case.get("message"),
        "fallback": bool(failure_class) or status == "failed" or is_fallback(answer),
        "status": status,
        "worker": route.get("worker") if isinstance(route, dict) else None,
        "intent": route.get("intent") if isinstance(route, dict) else None,
        "failure_class": failure_class,
        "trace_id": (final_payload or {}).get("trace_id") if isinstance(final_payload, dict) else None,
        "active_tools": _string_list(diagnostics.get("active_tools") if isinstance(diagnostics, dict) else None),
        "active_skills": _active_skill_ids(diagnostics.get("active_skills") if isinstance(diagnostics, dict) else None),
        "tool_calls": _tool_call_names(diagnostics.get("tools") if isinstance(diagnostics, dict) else None),
        "answer": answer,
        "agent_result": agent_result,
        "provider_issue": extract_provider_issue(final_payload or {}),
        "model_config": diagnostics.get("model_config") if isinstance(diagnostics.get("model_config"), dict) else None,
    }
    result["environment_failure"] = (
        isinstance(failure_class, str)
        and allowed_environment_failures is not None
        and failure_class in allowed_environment_failures
    )
    result["evaluation"] = evaluate_result(
        case,
        result,
        allowed_environment_failures=allowed_environment_failures,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay chat cases against local SmartEats backend")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument(
        "--cases",
        default="app/tests/fixtures/replay_cases.json",
        help="Replay cases JSON path",
    )
    parser.add_argument("--out", default="replay_report.json", help="Output JSON report path")
    parser.add_argument("--model-value", default=None, help="Optional model/provider value to send in chat stream payload")
    parser.add_argument("--max-cases", type=int, default=None, help="Optional maximum number of replay cases to run")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="HTTP timeout per replay request")
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=0.0,
        help="Delay between replay cases to reduce provider or local rate limiting",
    )
    parser.add_argument(
        "--allow-environment-failure-class",
        action="append",
        default=[],
        help="Failure class that should not fail no_fallback expectations, while contract/route checks still apply.",
    )
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    if args.max_cases is not None and args.max_cases > 0:
        cases = cases[: args.max_cases]
    allowed_environment_failures = {
        item.strip()
        for item in args.allow_environment_failure_class
        if isinstance(item, str) and item.strip()
    }
    results = []
    for index, case in enumerate(cases):
        if index and args.request_delay_seconds > 0:
            time.sleep(args.request_delay_seconds)
        results.append(
            run_case(
                args.base_url,
                case,
                model_value=args.model_value,
                allowed_environment_failures=allowed_environment_failures or None,
                timeout_seconds=max(args.timeout_seconds, 0.1),
            )
        )

    total = len(results)
    fallback_count = sum(1 for x in results if x.get("fallback"))
    environment_failure_count = sum(1 for x in results if _case_has_environment_failure(x))
    passed_count = sum(1 for x in results if (x.get("evaluation") or {}).get("passed"))

    report = {
        "total": total,
        "passed_count": passed_count,
        "pass_rate": (passed_count / total) if total else 0.0,
        "fallback_count": fallback_count,
        "fallback_rate": (fallback_count / total) if total else 0.0,
        "environment_failure_count": environment_failure_count,
        "environment_failure_rate": (environment_failure_count / total) if total else 0.0,
        "provider_issue_counts": provider_issue_counts(results, "code"),
        "provider_issue_category_counts": provider_issue_counts(results, "category"),
        "provider_action_counts": provider_issue_counts(results, "action"),
        "model_config_counts": model_config_counts(results),
        "active_tool_counts": active_value_counts(results, "active_tools"),
        "active_skill_counts": active_value_counts(results, "active_skills"),
        "tool_call_counts": active_value_counts(results, "tool_calls"),
        "worker_tool_counts": worker_tool_counts(results),
        "worker_tool_call_counts": worker_tool_call_counts(results),
        "worker_tool_boundary_violations": worker_tool_boundary_violations(results),
        "allowed_environment_failures": sorted(allowed_environment_failures),
        "model_value": args.model_value,
        "timeout_seconds": args.timeout_seconds,
        "request_delay_seconds": args.request_delay_seconds,
        "failed": [
            {
                "id": item.get("id"),
                "message": item.get("message"),
                "violations": (item.get("evaluation") or {}).get("violations") or [],
            }
            for item in results
            if not (item.get("evaluation") or {}).get("passed")
        ],
        "results": results,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed"]:
        raise SystemExit(1)


def _case_has_environment_failure(item: dict[str, Any]) -> bool:
    if item.get("environment_failure"):
        return True
    turns = item.get("turns")
    return isinstance(turns, list) and any(isinstance(turn, dict) and turn.get("environment_failure") for turn in turns)


def _http_error_result(
    case: dict[str, Any],
    response: Any,
    *,
    model_value: str | None = None,
    allowed_environment_failures: set[str] | None = None,
) -> dict[str, Any]:
    status_code = int(getattr(response, "status_code", 0) or 0)
    provider_issue = classify_http_provider_issue(status_code)
    expected_intent = _expected_string(case, None, "intent_in") or "unknown"
    expected_worker = _expected_worker(case, expected_intent)
    failure_class = "upstream_error"
    final = {
        "recommendations": [],
        "followups": [],
        "warnings": [provider_issue["user_message"]],
        "provider_issue": provider_issue,
    }
    result = {
        "id": case.get("id"),
        "message": case.get("message"),
        "fallback": True,
        "status": "failed",
        "worker": expected_worker,
        "intent": expected_intent,
        "failure_class": failure_class,
        "trace_id": f"http-error-{case.get('id') or 'unknown'}",
        "active_tools": [],
        "active_skills": [],
        "tool_calls": [],
        "answer": final,
        "agent_result": {
            "status": "failed",
            "worker": expected_worker,
            "trace_id": f"http-error-{case.get('id') or 'unknown'}",
            "failure_class": failure_class,
            "final": final,
            "diagnostics": {
                "route": {"worker": expected_worker, "intent": expected_intent},
                "provider_issue": provider_issue,
                "http_status": status_code,
            },
        },
        "provider_issue": provider_issue,
        "model_config": {"requested_model_value": model_value} if model_value else None,
        "environment_failure": (
            allowed_environment_failures is not None and failure_class in allowed_environment_failures
        ),
        "harness_environment_failure": True,
        "http_status": status_code,
    }
    result["evaluation"] = evaluate_result(
        case,
        result,
        allowed_environment_failures=allowed_environment_failures,
    )
    return result


def _request_error_result(
    case: dict[str, Any],
    error_kind: str,
    detail: str,
    *,
    model_value: str | None = None,
    allowed_environment_failures: set[str] | None = None,
) -> dict[str, Any]:
    provider_issue = classify_request_provider_issue(error_kind)
    provider_issue["detail"] = detail[:500]
    expected_intent = _expected_string(case, None, "intent_in") or "unknown"
    expected_worker = _expected_worker(case, expected_intent)
    failure_class = "upstream_error"
    final = {
        "recommendations": [],
        "followups": [],
        "warnings": [provider_issue["user_message"]],
        "provider_issue": provider_issue,
    }
    result = {
        "id": case.get("id"),
        "message": case.get("message"),
        "fallback": True,
        "status": "failed",
        "worker": expected_worker,
        "intent": expected_intent,
        "failure_class": failure_class,
        "trace_id": f"request-error-{case.get('id') or 'unknown'}",
        "active_tools": [],
        "active_skills": [],
        "tool_calls": [],
        "answer": final,
        "agent_result": {
            "status": "failed",
            "worker": expected_worker,
            "trace_id": f"request-error-{case.get('id') or 'unknown'}",
            "failure_class": failure_class,
            "final": final,
            "diagnostics": {
                "route": {"worker": expected_worker, "intent": expected_intent},
                "provider_issue": provider_issue,
                "request_error": error_kind,
            },
        },
        "provider_issue": provider_issue,
        "model_config": {"requested_model_value": model_value} if model_value else None,
        "environment_failure": (
            allowed_environment_failures is not None and failure_class in allowed_environment_failures
        ),
        "harness_environment_failure": True,
        "request_error": error_kind,
    }
    result["evaluation"] = evaluate_result(
        case,
        result,
        allowed_environment_failures=allowed_environment_failures,
    )
    return result


def classify_http_provider_issue(status_code: int) -> dict[str, Any]:
    if status_code == 429:
        return {
            "category": "provider_rate_limit",
            "code": "rate_limited",
            "http_status": status_code,
            "provider_error_code": "",
            "user_message": "模型或本地服务触发限流，请降低 live replay 频率或稍后重试。",
            "action": "wait_or_reduce_live_replay_rate",
        }
    return {
        "category": "upstream_http_error",
        "code": f"http_{status_code}" if status_code else "http_error",
        "http_status": status_code,
        "provider_error_code": "",
        "user_message": "模型或本地服务返回 HTTP 错误，replay 已记录为环境失败。",
        "action": "inspect_upstream_http_error",
    }


def classify_request_provider_issue(error_kind: str) -> dict[str, Any]:
    if error_kind == "timeout":
        return {
            "category": "provider_timeout",
            "code": "request_timeout",
            "http_status": None,
            "provider_error_code": "",
            "user_message": "live replay 等待模型或本地服务响应超时，请稍后重试或降低探测频率。",
            "action": "wait_or_reduce_live_replay_rate",
        }
    return {
        "category": "upstream_request_error",
        "code": "request_error",
        "http_status": None,
        "provider_error_code": "",
        "user_message": "live replay 请求模型或本地服务失败，已记录为环境失败。",
        "action": "inspect_upstream_request_error",
    }


def _expected_string(case: dict[str, Any], key: str | None, list_key: str | None) -> str | None:
    expect = case.get("expect")
    if not isinstance(expect, dict):
        return None
    if key:
        value = expect.get(key)
        if isinstance(value, str) and value:
            return value
    if list_key:
        values = expect.get(list_key)
        if isinstance(values, list):
            return next((item for item in values if isinstance(item, str) and item), None)
    return None


def _expected_worker(case: dict[str, Any], intent: str | None) -> str:
    worker = _expected_string(case, "worker", "worker_in")
    if worker:
        return worker
    return {
        "eat_out": "food_advisor",
        "decide_food": "food_advisor",
        "cook_home": "home_chef",
        "route": "route_planner",
        "travel": "travel_planner",
        "chat": "general_chat",
    }.get(intent or "", "unknown")


def extract_provider_issue(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("provider_issue")
    if isinstance(direct, dict):
        return direct
    answer = payload.get("answer")
    if isinstance(answer, dict) and isinstance(answer.get("provider_issue"), dict):
        return answer["provider_issue"]
    agent_result = payload.get("agent_result")
    if not isinstance(agent_result, dict):
        return None
    final = agent_result.get("final")
    if isinstance(final, dict) and isinstance(final.get("provider_issue"), dict):
        return final["provider_issue"]
    diagnostics = agent_result.get("diagnostics")
    if isinstance(diagnostics, dict) and isinstance(diagnostics.get("provider_issue"), dict):
        return diagnostics["provider_issue"]
    return None


def provider_issue_counts(results: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for issue in _iter_provider_issues(result):
            value = issue.get(key)
            if isinstance(value, str) and value:
                counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def model_config_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in _iter_result_items(results):
        model_config = item.get("model_config")
        if not isinstance(model_config, dict):
            continue
        value = (
            model_config.get("provider_value")
            or model_config.get("requested_model_value")
            or model_config.get("model_planner")
        )
        if isinstance(value, str) and value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def active_value_counts(results: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in _iter_result_items(results):
        values = item.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value:
                counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def worker_tool_counts(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return worker_value_counts(results, "active_tools")


def worker_tool_call_counts(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return worker_value_counts(results, "tool_calls")


def worker_value_counts(results: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in _iter_result_items(results):
        worker = item.get("worker")
        values = item.get(key)
        if not isinstance(worker, str) or not isinstance(values, list):
            continue
        bucket = counts.setdefault(worker, {})
        for value in values:
            if isinstance(value, str) and value:
                bucket[value] = bucket.get(value, 0) + 1
    return {
        worker: dict(sorted(value_counts.items(), key=lambda item: item[1], reverse=True))
        for worker, value_counts in sorted(counts.items())
    }


def worker_tool_boundary_violations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for item in _iter_result_items(results):
        item_violations = validate_worker_tool_boundary(item)
        if item_violations:
            violations.append(
                {
                    "id": item.get("id"),
                    "worker": item.get("worker"),
                    "active_tools": item.get("active_tools") or [],
                    "violations": item_violations,
                }
            )
    return violations


def _iter_provider_issues(item: dict[str, Any]):
    issue = item.get("provider_issue")
    if isinstance(issue, dict):
        yield issue
    turns = item.get("turns")
    if isinstance(turns, list):
        for turn in turns:
            if isinstance(turn, dict):
                turn_issue = turn.get("provider_issue")
                if isinstance(turn_issue, dict):
                    yield turn_issue


def _iter_result_items(results: list[dict[str, Any]]):
    for item in results:
        if isinstance(item, dict):
            yield item
            turns = item.get("turns")
            if isinstance(turns, list):
                for turn in turns:
                    if isinstance(turn, dict):
                        yield turn


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _tool_call_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("tool") or item.get("tool_name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def _active_skill_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return ids


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


if __name__ == "__main__":
    main()

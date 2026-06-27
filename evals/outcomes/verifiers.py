from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase


@dataclass
class OutcomeVerificationResult:
    verifier: str
    score: float
    passed: bool
    failures: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def verify_outcomes(case: EvalCase, trace: EvalTrace) -> list[OutcomeVerificationResult]:
    """Run outcome verifiers declared in case.expectations.

    These verifiers validate observable end state and side effects. DB state
    verification is represented as a diagnostic contract in v1; concrete table
    checks are added per business workflow once the expected DB effects are
    declared by dataset authors.
    """
    expectations = case.expectations or {}
    results = [
        _verify_schema_state(expectations.get("expected_final_state"), trace),
        _verify_tool_result_shape(expectations.get("expected_tool_result_shape"), trace),
        _verify_side_effect_guard(expectations.get("forbidden_db_effects"), trace),
        _verify_business_outcome(expectations.get("business_outcome"), case, trace),
        _verify_db_effect_contract(expectations.get("expected_db_effects"), trace),
    ]
    return [item for item in results if item is not None]


def _verify_schema_state(expected: Any, trace: EvalTrace) -> OutcomeVerificationResult | None:
    if not isinstance(expected, dict):
        return None
    final_json = trace.final_json if isinstance(trace.final_json, dict) else {}
    failures: list[dict[str, Any]] = []
    for key, expected_value in expected.items():
        actual = final_json.get(key)
        if actual != expected_value:
            failures.append({
                "field": key,
                "expected": expected_value,
                "actual": actual,
                "reason": "final_state_mismatch",
            })
    return OutcomeVerificationResult(
        verifier="schema_state_verifier",
        score=0.0 if failures else 1.0,
        passed=not failures,
        failures=failures,
        details={"checked_fields": sorted(expected.keys())},
    )


def _verify_tool_result_shape(expected: Any, trace: EvalTrace) -> OutcomeVerificationResult | None:
    if not isinstance(expected, dict):
        return None
    failures: list[dict[str, Any]] = []
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for step in trace.steps:
        if step.event_type != "tool_result":
            continue
        raw = step.raw_data if isinstance(step.raw_data, dict) else {}
        name = step.tool_name or raw.get("name")
        if name:
            by_tool.setdefault(str(name), []).append(raw)
    for tool_name, shape in expected.items():
        events = by_tool.get(str(tool_name), [])
        if not events:
            failures.append({"tool": tool_name, "reason": "missing_tool_result"})
            continue
        required_fields = shape.get("required_fields", []) if isinstance(shape, dict) else []
        for field_name in required_fields:
            if not any(_contains_key(event, str(field_name)) for event in events):
                failures.append({
                    "tool": tool_name,
                    "field": field_name,
                    "reason": "missing_required_field",
                })
    total_checks = max(1, sum(len(v.get("required_fields", [])) if isinstance(v, dict) else 1 for v in expected.values()))
    score = max(0.0, 1.0 - len(failures) / total_checks)
    return OutcomeVerificationResult(
        verifier="tool_result_verifier",
        score=score,
        passed=not failures,
        failures=failures,
        details={"checked_tools": sorted(str(name) for name in expected.keys())},
    )


def _verify_side_effect_guard(forbidden: Any, trace: EvalTrace) -> OutcomeVerificationResult | None:
    if not forbidden:
        return None
    items = forbidden if isinstance(forbidden, list) else [forbidden]
    final_json = trace.final_json if isinstance(trace.final_json, dict) else {}
    failures: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            field_name = item.get("field") or item.get("state_key")
            forbidden_value = item.get("value")
            if field_name and field_name in final_json and (forbidden_value is None or final_json.get(field_name) == forbidden_value):
                failures.append({"field": field_name, "value": final_json.get(field_name), "reason": "forbidden_final_state"})
        else:
            field_name = str(item)
            if field_name in final_json:
                failures.append({"field": field_name, "value": final_json.get(field_name), "reason": "forbidden_final_state"})
    return OutcomeVerificationResult(
        verifier="side_effect_guard",
        score=0.0 if failures else 1.0,
        passed=not failures,
        failures=failures,
        details={"forbidden_effects": items},
    )


def _verify_db_effect_contract(expected: Any, trace: EvalTrace) -> OutcomeVerificationResult | None:
    if not expected:
        return None
    return OutcomeVerificationResult(
        verifier="db_state_verifier",
        score=1.0,
        passed=True,
        failures=[],
        details={
            "mode": "contract_recorded",
            "expected_db_effects": expected,
            "note": "DB effect contracts are recorded in v1; workflow-specific table checks can be attached per dataset.",
            "trace_run_id": trace.run_id,
        },
    )


def _verify_business_outcome(expected: Any, case: EvalCase, trace: EvalTrace) -> OutcomeVerificationResult | None:
    if not isinstance(expected, dict):
        return None

    final_json = trace.final_json if isinstance(trace.final_json, dict) else {}
    text = trace.searchable_text
    failures: list[dict[str, Any]] = []
    total_checks = 0

    def check(condition: bool, failure: dict[str, Any]) -> None:
        nonlocal total_checks
        total_checks += 1
        if not condition:
            failures.append(failure)

    for field_name in expected.get("required_final_fields", []):
        check(
            bool(_get_path(final_json, str(field_name))),
            {"field": field_name, "reason": "missing_final_field"},
        )

    allowed_states = expected.get("allowed_states")
    if allowed_states:
        actual_state = _infer_business_state(final_json, case.scene.value)
        check(
            actual_state in set(str(item) for item in allowed_states),
            {
                "field": "state",
                "expected": allowed_states,
                "actual": actual_state,
                "reason": "unexpected_travel_state" if case.scene.value == "travel_planner" else "unexpected_state",
            },
        )

    recommendation_type = expected.get("recommendation_type")
    if recommendation_type:
        actual_types = {str(rec.get("type")) for rec in trace.recommendations if isinstance(rec, dict) and rec.get("type")}
        check(
            str(recommendation_type) in actual_types,
            {
                "field": "recommendations.type",
                "expected": recommendation_type,
                "actual": sorted(actual_types),
                "reason": "recommendation_type_mismatch",
            },
        )

    for field_name in expected.get("required_fields", []):
        check(
            any(bool(_get_path(rec, str(field_name))) for rec in trace.recommendations if isinstance(rec, dict)),
            {"field": f"recommendations.{field_name}", "reason": "missing_recommendation_field"},
        )

    for keyword in expected.get("must_contain", []):
        check(
            str(keyword) in text,
            {"keyword": keyword, "reason": "missing_required_text"},
        )

    if "max_price" in expected:
        max_price = _to_float(expected.get("max_price"))
        observed_prices = _extract_prices(trace)
        check(
            max_price is not None and bool(observed_prices) and min(observed_prices) <= max_price,
            {
                "field": "price",
                "expected_max": expected.get("max_price"),
                "actual": observed_prices,
                "reason": "price_exceeds_max_or_missing",
            },
        )

    if total_checks == 0:
        return OutcomeVerificationResult(
            verifier="business_outcome_verifier",
            score=1.0,
            passed=True,
            details={"scene": case.scene.value, "checked_fields": []},
        )

    score = max(0.0, 1.0 - len(failures) / total_checks)
    return OutcomeVerificationResult(
        verifier="business_outcome_verifier",
        score=score,
        passed=not failures,
        failures=failures,
        details={"scene": case.scene.value, "checks": total_checks},
    )


def _infer_business_state(final_json: dict[str, Any], scene: str) -> str | None:
    state = final_json.get("state")
    if state:
        return str(state)
    if scene == "travel_planner":
        if final_json.get("map"):
            return "map_generated"
        if final_json.get("itinerary"):
            return "itinerary_generated"
        if final_json.get("candidates") or final_json.get("places"):
            return "candidates_ready"
    return None


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _extract_prices(trace: EvalTrace) -> list[float]:
    prices: list[float] = []
    for rec in trace.recommendations:
        if not isinstance(rec, dict):
            continue
        for key in ("price", "avg_price", "cost", "budget"):
            value = _to_float(rec.get(key))
            if value is not None:
                prices.append(value)
    for match in re.finditer(r"(?:人均|约|￥|¥)?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", trace.searchable_text):
        value = _to_float(match.group(1))
        if value is not None:
            prices.append(value)
    return prices


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        if key in value:
            return True
        return any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    if isinstance(value, str):
        return key in value
    return False

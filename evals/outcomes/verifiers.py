from __future__ import annotations

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

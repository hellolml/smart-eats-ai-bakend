"""Fixture runner for deterministic offline evaluation."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from evals.adapters.sse_adapter import SSEAdapter
from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase


class FixtureRunner:
    """Build EvalTrace objects from checked-in fixture SSE events.

    This runner never calls the backend or external services. It keeps PR
    evaluation focused on dataset loading, trace parsing, evaluators, scoring,
    reporting, and thresholds.
    """

    def __init__(self, fixture_path: str = "./evals/datasets/fixture_traces.json"):
        self.fixture_path = Path(fixture_path)
        self.adapter = SSEAdapter()
        self._fixtures = self._load_fixtures()

    @property
    def case_ids(self) -> set[str]:
        return set(self._fixtures)

    async def run_case(self, case: EvalCase, trial_number: int = 0) -> EvalTrace:
        fixture = self._fixtures.get(case.id)
        trace = EvalTrace(
            run_id=f"fixture-{case.id}-{trial_number}",
            case_id=case.id,
            trial_number=trial_number,
            expected_scene=case.scene.value,
            started_at_monotonic=time.monotonic(),
        )
        if not fixture:
            trace.error = f"Missing fixture trace for case {case.id}"
            trace.error_reason = "missing_fixture_trace"
            return trace

        for event in fixture.get("events", []):
            event_type = event.get("event")
            data = event.get("data", {})
            if event_type and isinstance(data, dict):
                self.adapter._record_event(trace, str(event_type), data)

        if "total_duration_ms" in fixture:
            trace.total_duration_ms = float(fixture["total_duration_ms"])
        if trace.total_duration_ms == 0:
            trace.total_duration_ms = (time.monotonic() - (trace.started_at_monotonic or time.monotonic())) * 1000
        if not trace.actual_scene:
            trace.error_reason = trace.error_reason or "missing_actual_route"
        return trace

    def _load_fixtures(self) -> dict[str, dict[str, Any]]:
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            items = data.get("traces", [])
        else:
            items = data
        fixtures: dict[str, dict[str, Any]] = {}
        for item in items:
            case_id = item.get("case_id")
            if case_id:
                fixtures[str(case_id)] = item
        return fixtures

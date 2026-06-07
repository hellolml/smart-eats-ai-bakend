"""ConstraintEvaluator — 业务约束满足度评测器."""
from __future__ import annotations

import re
from typing import Any

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


class ConstraintEvaluator(BaseEvaluator):
    """评估预算、推荐类型、关键词、食材、地点等可规则判定约束。"""

    @property
    def name(self) -> str:
        return "constraint"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        output_expect = case.get_output_expectation()
        checks: list[float] = []

        if output_expect.recommendations_type:
            checks.append(self._recommendation_type_score(trace, output_expect.recommendations_type))

        if output_expect.must_contain:
            checks.append(self._contains_score(trace.searchable_text, output_expect.must_contain))

        must_satisfy = output_expect.must_satisfy or {}

        if "budget_max" in must_satisfy:
            checks.append(self._budget_score(trace, must_satisfy["budget_max"]))

        if "location_hint" in must_satisfy:
            checks.append(self._text_contains_score(trace.searchable_text, str(must_satisfy["location_hint"])))

        fridge_items = case.initial_context.get("fridge_items")
        if fridge_items:
            checks.append(self._contains_score(trace.searchable_text, [str(item) for item in fridge_items]))

        score = sum(checks) / len(checks) if checks else 1.0

        return {"constraint_satisfaction": score}

    def _recommendation_type_score(self, trace: EvalTrace, expected: str) -> float:
        if not trace.recommendations:
            return 0.0
        actual_types = {str(rec.get("type")) for rec in trace.recommendations if isinstance(rec, dict)}
        return 1.0 if expected in actual_types else 0.0

    def _contains_score(self, text: str, keywords: list[str]) -> float:
        if not keywords:
            return 1.0
        return sum(1 for keyword in keywords if keyword and keyword in text) / len(keywords)

    def _text_contains_score(self, text: str, keyword: str) -> float:
        return 1.0 if keyword and keyword in text else 0.0

    def _budget_score(self, trace: EvalTrace, budget_max: Any) -> float:
        try:
            max_price = float(budget_max)
        except (TypeError, ValueError):
            return 0.0

        prices: list[float] = []
        for rec in trace.recommendations:
            if not isinstance(rec, dict):
                continue
            price = rec.get("price")
            if isinstance(price, (int, float)):
                prices.append(float(price))

        if not prices:
            for match in re.finditer(r"(?:人均|约|￥|¥)?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", trace.searchable_text):
                prices.append(float(match.group(1)))

        if not prices:
            return 0.0
        return 1.0 if min(prices) <= max_price else 0.0

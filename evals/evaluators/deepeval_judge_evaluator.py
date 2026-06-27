"""Optional DeepEval LLM Judge evaluator for scheduled/manual runs."""
from __future__ import annotations

import os

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator
from evals.rubric import get_dimension_description, get_dimension_rubric, get_rubric_dimensions, get_rubric_version


class DeepEvalJudgeEvaluator(BaseEvaluator):
    """DeepEval GEval wrapper.

    This evaluator is intentionally not part of the default PR gate. It returns
    skipped metadata when dependencies or model credentials are unavailable.
    """

    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold
        self.rubric_version = get_rubric_version()
        self.dimensions = get_rubric_dimensions()

    @property
    def name(self) -> str:
        return "deepeval_judge"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        if os.getenv("DEEPEVAL_JUDGE_ENABLED", "false").lower() not in {"1", "true", "yes"}:
            trace.judge_skipped_reason = "DEEPEVAL_JUDGE_ENABLED is false"
            return {"llm_judge_skipped": 1.0}

        try:
            from deepeval.metrics import GEval  # type: ignore
            from deepeval.test_case import LLMTestCase, SingleTurnParams  # type: ignore
        except Exception:
            trace.judge_skipped_reason = "deepeval dependency unavailable"
            return {"llm_judge_skipped": 1.0}

        test_case = LLMTestCase(
            input=case.task,
            actual_output=trace.searchable_text,
            expected_output=str(case.expectations),
        )
        params = [
            getattr(SingleTurnParams, "INPUT", None),
            getattr(SingleTurnParams, "ACTUAL_OUTPUT", None),
            getattr(SingleTurnParams, "EXPECTED_OUTPUT", None),
        ]
        evaluation_params = [item for item in params if item is not None]

        scores: dict[str, float] = {}
        reasons: dict[str, str] = {}
        try:
            for dimension in self.dimensions:
                metric = GEval(
                    name=dimension,
                    criteria=self._criteria_for_dimension(dimension),
                    evaluation_params=evaluation_params,
                    threshold=self.threshold,
                )
                metric.measure(test_case)
                scores[dimension] = min(1.0, max(0.0, float(metric.score or 0.0)))
                reasons[dimension] = str(getattr(metric, "reason", "") or "")
        except Exception as exc:
            trace.judge_skipped_reason = f"deepeval measure failed: {exc}"
            return {"llm_judge_skipped": 1.0}

        scores["llm_judge_skipped"] = 0.0
        trace.judge_scores = {k: v for k, v in scores.items() if k != "llm_judge_skipped"}
        trace.judge_reasons = reasons
        trace.judge_skipped_reason = None
        return scores

    def _criteria_for_dimension(self, dimension: str) -> str:
        return (
            f"Evaluate only the Smart-Eats answer dimension `{dimension}`.\n"
            f"Description: {get_dimension_description(dimension)}\n"
            f"Rubric:\n{get_dimension_rubric(dimension)}\n"
            "Return a score from 0 to 1 where higher is better. "
            "If there is insufficient information, assign a conservative score and explain why."
        )

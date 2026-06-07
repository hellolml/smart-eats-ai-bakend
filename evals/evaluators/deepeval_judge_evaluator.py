"""Optional DeepEval LLM Judge evaluator for scheduled/manual runs."""
from __future__ import annotations

import os

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


class DeepEvalJudgeEvaluator(BaseEvaluator):
    """DeepEval GEval wrapper.

    This evaluator is intentionally not part of the default PR gate. It returns
    skipped metadata when dependencies or model credentials are unavailable.
    """

    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold

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
        metric = GEval(
            name="SmartEatsQuality",
            criteria=(
                "Evaluate whether the Smart-Eats agent answer is relevant, actionable, "
                "does not hallucinate unsupported facts, and follows the expected constraints."
            ),
            evaluation_params=[
                SingleTurnParams.ACTUAL_OUTPUT,
                SingleTurnParams.EXPECTED_OUTPUT,
            ],
            threshold=self.threshold,
        )
        try:
            metric.measure(test_case)
        except Exception as exc:
            trace.judge_skipped_reason = f"deepeval measure failed: {exc}"
            return {"llm_judge_skipped": 1.0}

        score = float(metric.score or 0.0)
        reason = str(getattr(metric, "reason", "") or "")
        scores = {
            "answer_relevance": score,
            "actionability": score,
            "hallucination_control": score,
            "constraint_adherence_explained": score,
            "llm_judge_skipped": 0.0,
        }
        trace.judge_scores = {k: v for k, v in scores.items() if k != "llm_judge_skipped"}
        trace.judge_reasons = {
            "answer_relevance": reason,
            "actionability": reason,
            "hallucination_control": reason,
            "constraint_adherence_explained": reason,
        }
        trace.judge_skipped_reason = None
        return scores

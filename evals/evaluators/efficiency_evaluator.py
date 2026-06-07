"""EfficiencyEvaluator — 将轨迹统计转换为 0-1 效率分."""
from __future__ import annotations

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


class EfficiencyEvaluator(BaseEvaluator):
    """评估工具调用数量、重复动作和耗时。"""

    @property
    def name(self) -> str:
        return "efficiency"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        tool_calls = len(trace.tool_calls)
        repeated = 0
        for i in range(1, len(trace.tool_calls)):
            if (
                trace.tool_calls[i].tool_name == trace.tool_calls[i - 1].tool_name
                and trace.tool_calls[i].tool_input == trace.tool_calls[i - 1].tool_input
            ):
                repeated += 1

        repeated_penalty = min(0.4, repeated * 0.2)
        tool_penalty = min(0.3, max(0, tool_calls - 3) * 0.075)
        latency_penalty = 0.0
        if trace.total_duration_ms:
            latency_penalty = min(0.3, max(0.0, trace.total_duration_ms - 10_000) / 30_000)

        efficiency = max(0.0, 1.0 - repeated_penalty - tool_penalty - latency_penalty)

        return {
            "efficiency": efficiency,
            "avg_steps": float(tool_calls),
            "repeated_action_rate": self._safe_divide(repeated, max(0, tool_calls - 1), 0.0),
        }

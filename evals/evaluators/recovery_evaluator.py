"""RecoveryEvaluator — 恢复能力评测器.

评估 Agent 在遇到工具错误时是否走了正确的恢复路径。
"""
from __future__ import annotations

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


class RecoveryEvaluator(BaseEvaluator):
    """恢复能力评测器"""

    @property
    def name(self) -> str:
        return "recovery"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        recovery_expect = case.get_recovery_expectation()

        # 如果无恢复期望，默认满分
        if not recovery_expect:
            return {"recovery_score": 1.0}

        # 1. 是否有 recovery 事件
        has_recovery = 1.0 if trace.recovery_events else 0.0

        # 2. 恢复路径是否匹配
        path_match = 0.0
        for event in trace.recovery_events:
            if event.path == recovery_expect.expected_path:
                path_match = 1.0
                break
            # 模糊匹配：clarify / best_effort / error_handling 等都算有效恢复
            if event.path in ("clarify", "best_effort_fallback", "error_handling"):
                path_match = 0.7  # 部分匹配

        # 3. 恢复后状态是否正确
        state_correct = 0.0
        if trace.state_value:
            if trace.state_value == recovery_expect.expected_state:
                state_correct = 1.0
            elif trace.state_value != "fallback":
                # 没走 fallback 也算部分成功
                state_correct = 0.5

        # 4. 是否成功避免了直接 fallback
        avoided_fallback = 0.0 if trace.is_fallback else 1.0

        # 综合评分
        score = (
            has_recovery * 0.3
            + path_match * 0.3
            + state_correct * 0.2
            + avoided_fallback * 0.2
        )

        return {
            "recovery_score": score,
            "has_recovery_event": has_recovery,
            "recovery_path_match": path_match,
            "recovery_state_correct": state_correct,
            "avoided_fallback": avoided_fallback,
        }

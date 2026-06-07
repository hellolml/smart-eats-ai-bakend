"""ToolEvaluator — 工具调用正确性评测器.

评估工具调用是否符合期望（必须调用/禁止调用/参数正确性/重复调用检测）。
"""
from __future__ import annotations

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


class ToolEvaluator(BaseEvaluator):
    """工具调用正确性评测器"""

    @property
    def name(self) -> str:
        return "tool"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        tool_calls = trace.tool_call_names
        tool_expect = case.get_tool_expectation()

        # 1. 工具调用准确性（必须调用+禁止调用）
        accuracy_result = self._check_tool_expectation(tool_calls, tool_expect)

        # 2. 重复调用检测（连续调用同一工具且参数相同）
        repeated = 0
        for i in range(1, len(trace.tool_calls)):
            curr = trace.tool_calls[i]
            prev = trace.tool_calls[i - 1]
            if curr.tool_name == prev.tool_name:
                # 简单判断：同工具连续调用算重复
                if curr.tool_input == prev.tool_input:
                    repeated += 1

        repeated_rate = self._safe_divide(repeated, len(trace.tool_calls) - 1)

        # 3. 工具调用次数
        tool_call_count = len(trace.tool_calls)

        # 4. 参数非空检查（必调工具的参数不应为空）
        param_score = self._check_param_completeness(trace)

        return {
            **accuracy_result,
            "repeated_action_rate": repeated_rate,
            "tool_call_count": float(tool_call_count),
            "param_completeness": param_score,
        }

    def _check_param_completeness(self, trace: EvalTrace) -> float:
        """检查工具参数完整性（非空、非默认值）"""
        tool_steps = trace.tool_calls
        if not tool_steps:
            return 1.0

        complete = 0
        for step in tool_steps:
            if step.tool_input is None or step.tool_input == {}:
                # 工具无参数也算完整
                complete += 1
            elif isinstance(step.tool_input, dict):
                # 检查参数是否全为空值
                has_meaningful = any(
                    v is not None and v != "" and v != 0
                    for v in step.tool_input.values()
                )
                complete += 1 if has_meaningful else 0
            else:
                complete += 1

        return self._safe_divide(complete, len(tool_steps), 1.0)

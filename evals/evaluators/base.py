"""BaseEvaluator — 评测器抽象基类.

所有评测器必须继承此类并实现 evaluate 方法。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase, ToolExpectation


class BaseEvaluator(ABC):
    """评测器基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """评测器名称"""
        ...

    @abstractmethod
    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        """评估一次执行，返回各维度评分（0-1）

        Args:
            case: 评测用例
            trace: 执行轨迹

        Returns:
            各维度的评分字典，值域 [0, 1]
        """
        ...

    def _check_tool_expectation(
        self,
        tool_calls: list[str],
        expectation: ToolExpectation,
    ) -> dict[str, float]:
        """通用工具期望检查"""
        if not expectation.required and not expectation.forbidden:
            return {"tool_accuracy": 1.0}

        total_checks = len(expectation.required) + len(expectation.forbidden)
        if total_checks == 0:
            return {"tool_accuracy": 1.0}

        passed = 0
        for tool in expectation.required:
            if tool in tool_calls:
                passed += 1
        for tool in expectation.forbidden:
            if tool not in tool_calls:
                passed += 1

        return {"tool_accuracy": passed / total_checks}

    @staticmethod
    def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
        """安全除法"""
        return numerator / denominator if denominator > 0 else default

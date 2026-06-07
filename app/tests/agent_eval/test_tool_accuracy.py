"""工具调用评测 — 验证工具选择正确性."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.adapters.trace import EvalTrace, StepTrace
from evals.datasets.eval_case import EvalCase, Scene
from evals.evaluators.tool_evaluator import ToolEvaluator


class TestToolAccuracy:
    """工具调用准确性测试"""

    @pytest.fixture
    def evaluator(self) -> ToolEvaluator:
        return ToolEvaluator()

    def test_required_tool_called(self, evaluator: ToolEvaluator):
        """必须调用的工具被调用"""
        from evals.datasets.eval_case import Category
        case = EvalCase(
            id="test-001",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={
                "tools": {
                    "required": ["search_restaurants"],
                    "forbidden": [],
                }
            },
        )
        trace = EvalTrace(
            run_id="test", case_id="test-001", trial_number=0,
        )
        trace.steps.append(StepTrace(
            step_number=0, event_type="tool_call", tool_name="search_restaurants",
        ))

        scores = evaluator.evaluate(case, trace)
        assert scores["tool_accuracy"] == 1.0

    def test_forbidden_tool_not_called(self, evaluator: ToolEvaluator):
        """禁止调用的工具未被调用"""
        from evals.datasets.eval_case import Category
        case = EvalCase(
            id="test-002",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={
                "tools": {
                    "required": [],
                    "forbidden": ["plan_route"],
                }
            },
        )
        trace = EvalTrace(
            run_id="test", case_id="test-002", trial_number=0,
        )
        trace.steps.append(StepTrace(
            step_number=0, event_type="tool_call", tool_name="search_restaurants",
        ))

        scores = evaluator.evaluate(case, trace)
        assert scores["tool_accuracy"] == 1.0

    def test_forbidden_tool_called_fails(self, evaluator: ToolEvaluator):
        """禁止调用的工具被调用应扣分"""
        from evals.datasets.eval_case import Category
        case = EvalCase(
            id="test-003",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={
                "tools": {
                    "required": [],
                    "forbidden": ["plan_route"],
                }
            },
        )
        trace = EvalTrace(
            run_id="test", case_id="test-003", trial_number=0,
        )
        trace.steps.append(StepTrace(
            step_number=0, event_type="tool_call", tool_name="plan_route",
        ))

        scores = evaluator.evaluate(case, trace)
        assert scores["tool_accuracy"] == 0.0

    def test_repeated_action_detected(self, evaluator: ToolEvaluator):
        """重复调用应被检测"""
        from evals.datasets.eval_case import Category
        case = EvalCase(
            id="test-004",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={"tools": {}},
        )
        trace = EvalTrace(
            run_id="test", case_id="test-004", trial_number=0,
        )
        # 两次相同调用
        trace.steps.append(StepTrace(
            step_number=0, event_type="tool_call", tool_name="search_restaurants",
            tool_input={"query": "火锅"},
        ))
        trace.steps.append(StepTrace(
            step_number=1, event_type="tool_call", tool_name="search_restaurants",
            tool_input={"query": "火锅"},
        ))

        scores = evaluator.evaluate(case, trace)
        assert scores["repeated_action_rate"] > 0, "Repeated actions should be detected"

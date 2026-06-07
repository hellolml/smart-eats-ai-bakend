"""任务完成评测 — 验证 Agent 任务成功率."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import Category, EvalCase, Scene
from evals.evaluators.task_evaluator import TaskEvaluator


class TestTaskCompletion:
    """任务完成评测"""

    @pytest.fixture
    def evaluator(self) -> TaskEvaluator:
        return TaskEvaluator()

    def test_fallback_detected(self, evaluator: TaskEvaluator):
        """fallback 回答应被标记为失败"""
        case = EvalCase(
            id="test-fallback",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={
                "output": {"state_not": "fallback"},
            },
        )
        trace = EvalTrace(
            run_id="test", case_id="test-fallback", trial_number=0,
            final_json={
                "recommendations": [{"type": "note", "title": "test", "reason": "fallback"}],
            },
        )

        scores = evaluator.evaluate(case, trace)
        assert scores["no_fallback"] == 0.0
        assert scores["task_success"] < 0.7  # fallback 应显著拉低分数

    def test_successful_recommendation(self, evaluator: TaskEvaluator):
        """有实质推荐内容应得分较高"""
        case = EvalCase(
            id="test-success",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="火锅推荐",
            expectations={
                "output": {
                    "state_not": "fallback",
                    "must_contain": ["火锅"],
                },
            },
        )
        trace = EvalTrace(
            run_id="test", case_id="test-success", trial_number=0,
            final_json={
                "recommendations": [{"type": "restaurant", "title": "火锅店", "reason": "推荐"}],
                "raw_text": "推荐这家火锅店，味道不错",
            },
        )

        scores = evaluator.evaluate(case, trace)
        assert scores["no_fallback"] == 1.0
        assert scores["has_content"] == 1.0
        assert scores["must_contain_score"] == 1.0
        assert scores["task_success"] > 0.7

    def test_empty_response(self, evaluator: TaskEvaluator):
        """空回应应得低分"""
        case = EvalCase(
            id="test-empty",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={"output": {"state_not": "fallback"}},
        )
        trace = EvalTrace(
            run_id="test", case_id="test-empty", trial_number=0,
            final_json={},
        )

        scores = evaluator.evaluate(case, trace)
        assert scores["has_content"] == 0.0

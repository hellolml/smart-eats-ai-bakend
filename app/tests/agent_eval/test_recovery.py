"""恢复能力评测 — 验证 Agent 错误恢复路径."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.adapters.trace import EvalTrace, RecoveryEvent
from evals.datasets.eval_case import Category, EvalCase, Scene
from evals.evaluators.recovery_evaluator import RecoveryEvaluator


class TestRecovery:
    """恢复能力评测"""

    @pytest.fixture
    def evaluator(self) -> RecoveryEvaluator:
        return RecoveryEvaluator()

    def test_no_recovery_expectation(self, evaluator: RecoveryEvaluator):
        """无恢复期望时应满分"""
        case = EvalCase(
            id="test-no-recovery",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={},
        )
        trace = EvalTrace(run_id="test", case_id="test-no-recovery", trial_number=0)

        scores = evaluator.evaluate(case, trace)
        assert scores["recovery_score"] == 1.0

    def test_correct_recovery_path(self, evaluator: RecoveryEvaluator):
        """正确恢复路径应得高分"""
        case = EvalCase(
            id="test-recovery-ok",
            category=Category.TOOL_FAILURE,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={
                "recovery": {
                    "trigger": "empty_result",
                    "expected_path": "best_effort_fallback",
                    "expected_state": "note",
                }
            },
        )
        trace = EvalTrace(
            run_id="test", case_id="test-recovery-ok", trial_number=0,
            final_json={"state": "note", "recommendations": [{"type": "note", "title": "暂无结果"}]},
        )
        trace.recovery_events.append(RecoveryEvent(
            path="best_effort_fallback", trigger="empty_result", message="搜索为空",
        ))

        scores = evaluator.evaluate(case, trace)
        assert scores["has_recovery_event"] == 1.0
        assert scores["recovery_path_match"] == 1.0
        assert scores["recovery_score"] > 0.7

    def test_missing_recovery(self, evaluator: RecoveryEvaluator):
        """有恢复期望但无恢复事件应得低分"""
        case = EvalCase(
            id="test-no-recovery-event",
            category=Category.TOOL_FAILURE,
            scene=Scene.EAT_OUT,
            task="test",
            expectations={
                "recovery": {
                    "trigger": "empty_result",
                    "expected_path": "best_effort_fallback",
                    "expected_state": "note",
                }
            },
        )
        trace = EvalTrace(
            run_id="test", case_id="test-no-recovery-event", trial_number=0,
        )

        scores = evaluator.evaluate(case, trace)
        assert scores["has_recovery_event"] == 0.0
        assert scores["recovery_score"] < 0.5

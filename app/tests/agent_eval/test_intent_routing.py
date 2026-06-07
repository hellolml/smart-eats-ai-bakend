"""意图路由评测 — 验证 Supervisor 路由准确性."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import Category, EvalCase, Scene
from evals.evaluators.intent_evaluator import IntentEvaluator


def _load_cases() -> list[EvalCase]:
    """从数据集目录加载用例"""
    dataset_dir = project_root / "evals" / "datasets"
    cases: list[EvalCase] = []
    if not dataset_dir.exists():
        return []

    for json_file in dataset_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    cases.append(EvalCase.from_dict(item))
            elif isinstance(data, dict):
                cases.append(EvalCase.from_dict(data))
        except Exception:
            pass

    for jsonl_file in dataset_dir.glob("*.jsonl"):
        try:
            content = jsonl_file.read_text(encoding="utf-8").strip()
            if not content:
                continue
            # 兼容 JSON 数组格式
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    for item in data:
                        cases.append(EvalCase.from_dict(item))
                    continue
            except json.JSONDecodeError:
                pass
            for line in content.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                cases.append(EvalCase.from_dict(item))
        except Exception:
            pass

    return cases


class TestIntentRouting:
    """意图路由准确性测试"""

    @pytest.fixture
    def evaluator(self) -> IntentEvaluator:
        return IntentEvaluator()

    @pytest.fixture
    def all_cases(self) -> list[EvalCase]:
        return _load_cases()

    def test_dataset_loaded(self, all_cases: list[EvalCase]):
        """数据集应能正确加载"""
        assert len(all_cases) > 0, "No eval cases found in dataset"

    def test_eat_out_intent_detected(self, evaluator: IntentEvaluator, all_cases: list[EvalCase]):
        """eat_out 场景用例应正确设置意图"""
        eat_out_cases = [c for c in all_cases if c.scene == Scene.EAT_OUT]
        assert len(eat_out_cases) > 0, "No eat_out cases found"

        for case in eat_out_cases:
            assert case.expectations.get("intent") in ("eat_out", None), f"Case {case.id}: intent mismatch"

    def test_cook_home_intent_detected(self, evaluator: IntentEvaluator, all_cases: list[EvalCase]):
        """cook_home 场景用例应正确设置意图"""
        cook_cases = [c for c in all_cases if c.scene == Scene.COOK_HOME]
        assert len(cook_cases) > 0, "No cook_home cases found"

        for case in cook_cases:
            assert case.expectations.get("intent") in ("cook_home", None), f"Case {case.id}: intent mismatch"

    def test_route_intent_detected(self, evaluator: IntentEvaluator, all_cases: list[EvalCase]):
        """route 场景用例应正确设置意图"""
        route_cases = [c for c in all_cases if c.scene == Scene.ROUTE]
        assert len(route_cases) > 0, "No route cases found"

        for case in route_cases:
            assert case.expectations.get("intent") in ("route", None), f"Case {case.id}: intent mismatch"

    def test_chat_intent_detected(self, evaluator: IntentEvaluator, all_cases: list[EvalCase]):
        """chat 场景用例应正确设置意图"""
        chat_cases = [c for c in all_cases if c.scene == Scene.CHAT]
        assert len(chat_cases) > 0, "No chat cases found"

        for case in chat_cases:
            assert case.expectations.get("intent") in ("chat", None), f"Case {case.id}: intent mismatch"

    def test_evaluator_scores_format(self, evaluator: IntentEvaluator):
        """评测器应返回正确格式的分数"""
        case = EvalCase(
            id="test-format",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="测试",
            expectations={"intent": "eat_out"},
        )
        trace = EvalTrace(
            run_id="test",
            case_id="test-format",
            trial_number=0,
            scene="eat_out",
        )

        scores = evaluator.evaluate(case, trace)

        assert isinstance(scores, dict)
        assert "intent_accuracy" in scores
        assert "intent_overall" in scores
        assert all(0 <= v <= 1 for v in scores.values()), f"Scores out of range: {scores}"

    def test_correct_intent_gets_high_score(self, evaluator: IntentEvaluator):
        """正确的意图应得到高分"""
        case = EvalCase(
            id="test-correct",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="想吃火锅",
            expectations={"intent": "eat_out"},
        )
        trace = EvalTrace(
            run_id="test", case_id="test-correct", trial_number=0, scene="eat_out",
        )

        scores = evaluator.evaluate(case, trace)
        assert scores["intent_accuracy"] == 1.0

    def test_wrong_intent_gets_low_score(self, evaluator: IntentEvaluator):
        """错误的意图应得到低分"""
        case = EvalCase(
            id="test-wrong",
            category=Category.NORMAL,
            scene=Scene.EAT_OUT,
            task="想吃火锅",
            expectations={"intent": "eat_out"},
        )
        trace = EvalTrace(
            run_id="test", case_id="test-wrong", trial_number=0, scene="cook_home",
        )

        scores = evaluator.evaluate(case, trace)
        assert scores["intent_accuracy"] == 0.0

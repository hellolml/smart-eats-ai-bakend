"""IntentEvaluator — 意图路由准确性评测器.

评估 Supervisor 是否将用户请求路由到正确的 Worker。
"""
from __future__ import annotations

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


class IntentEvaluator(BaseEvaluator):
    """意图路由准确性评测器"""

    @property
    def name(self) -> str:
        return "intent"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        expected_intent = case.expectations.get("intent")
        expected_worker = case.expectations.get("worker")
        expected_skills = case.expectations.get("skills", [])

        # 从 trace 中提取实际路由结果；不得用 case.scene 作为实际值回填
        actual_scene = trace.actual_scene or trace.scene or ""
        actual_worker = trace.actual_worker or ""

        # 意图匹配
        intent_match = 0.0
        if expected_intent:
            # scene 可能是 travel_planner 而非 travel
            normalized = actual_scene.replace("_planner", "")
            intent_match = 1.0 if normalized == expected_intent or actual_scene == expected_intent else 0.0

        missing_route = not actual_scene and not actual_worker

        # Worker 路由匹配
        worker_match = 0.0 if missing_route else 1.0
        if expected_worker:
            worker_match = 1.0 if actual_worker == expected_worker else 0.0

        # Skill 激活匹配
        skill_match = 1.0
        if expected_skills:
            actual_skill = trace.skill or ",".join(trace.active_skills)
            # 检查是否有任一 expected_skill 出现在 actual_skill 中
            matched = any(es in actual_skill for es in expected_skills) if actual_skill else False
            skill_match = 1.0 if matched else 0.0

        # 综合评分
        intent_accuracy = intent_match
        overall = intent_match * 0.5 + worker_match * 0.3 + skill_match * 0.2

        return {
            "intent_accuracy": intent_accuracy,
            "worker_routing": worker_match,
            "skill_activation": skill_match,
            "intent_overall": overall,
        }

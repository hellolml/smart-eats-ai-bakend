"""SchemaEvaluator — 结构合规性评测器.

评估 FinalAnswerArgs JSON 是否符合 Schema（字段完整、类型正确）。
"""
from __future__ import annotations

from typing import Any

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


# FinalAnswerArgs 必需字段（参考 app/agent/schemas.py）
REQUIRED_FIELDS = {"recommendations", "followups", "warnings"}
OPTIONAL_FIELDS = {"state", "await_confirmation", "trip_meta", "sources",
                   "places", "candidates", "failed_places", "itinerary",
                   "map", "raw_text", "agent_id", "plan_type"}

# Recommendation 类型必需字段
REC_REQUIRED_BY_TYPE = {
    "recipe": {"type", "title"},
    "restaurant": {"type", "title"},
    "note": {"type", "title"},
}


class SchemaEvaluator(BaseEvaluator):
    """结构合规性评测器"""

    @property
    def name(self) -> str:
        return "schema"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        output_expect = case.get_output_expectation()

        # 如果不要求 schema 合规，直接满分
        if not output_expect.schema_compliant:
            return {"schema_compliance": 1.0}

        final = trace.final_json
        if final is None:
            return {"schema_compliance": 0.0}

        if not isinstance(final, dict):
            return {"schema_compliance": 0.0}

        # 1. 顶层字段完整性
        top_level_score = self._check_top_level(final)

        # 2. recommendations 结构检查
        rec_score = self._check_recommendations(final.get("recommendations"))

        # 3. 类型正确性检查
        type_score = self._check_types(final)

        # 综合
        compliance = top_level_score * 0.3 + rec_score * 0.4 + type_score * 0.3

        return {
            "schema_compliance": compliance,
            "top_level_completeness": top_level_score,
            "rec_structure_score": rec_score,
            "type_correctness": type_score,
        }

    def _check_top_level(self, final: dict[str, Any]) -> float:
        """检查顶层必需字段是否存在"""
        present = sum(1 for f in REQUIRED_FIELDS if f in final)
        return self._safe_divide(present, len(REQUIRED_FIELDS), 1.0)

    def _check_recommendations(self, recs: Any) -> float:
        """检查 recommendations 中每个条目的结构"""
        if not isinstance(recs, list):
            # recommendations 可以为空列表
            return 1.0 if recs is None else 0.0

        if not recs:
            return 1.0  # 空列表也合法

        scores = []
        for rec in recs:
            if not isinstance(rec, dict):
                scores.append(0.0)
                continue

            rec_type = rec.get("type")
            required = REC_REQUIRED_BY_TYPE.get(rec_type, {"type", "title"})
            present = sum(1 for f in required if f in rec)
            scores.append(self._safe_divide(present, len(required), 1.0))

        return sum(scores) / len(scores) if scores else 1.0

    def _check_types(self, final: dict[str, Any]) -> float:
        """检查字段类型正确性"""
        checks = 0
        passed = 0

        # recommendations 应为 list
        if "recommendations" in final:
            checks += 1
            if isinstance(final["recommendations"], list):
                passed += 1

        # followups 应为 list
        if "followups" in final:
            checks += 1
            if isinstance(final["followups"], list):
                passed += 1

        # warnings 应为 list
        if "warnings" in final:
            checks += 1
            if isinstance(final["warnings"], list):
                passed += 1

        # state 应为 str 或 None
        if "state" in final:
            checks += 1
            if isinstance(final["state"], (str, type(None))):
                passed += 1

        # await_confirmation 应为 bool 或 None
        if "await_confirmation" in final:
            checks += 1
            if isinstance(final["await_confirmation"], (bool, type(None))):
                passed += 1

        return self._safe_divide(passed, checks, 1.0)

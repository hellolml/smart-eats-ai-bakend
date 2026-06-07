"""TaskEvaluator — 任务成功率评测器.

评估 Agent 是否成功完成任务（非 fallback + 有实质内容 + 关键词命中）。
"""
from __future__ import annotations

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator


class TaskEvaluator(BaseEvaluator):
    """任务成功率评测器"""

    @property
    def name(self) -> str:
        return "task"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        output_expect = case.get_output_expectation()
        # 1. 非 fallback 检查
        no_fallback = 0.0 if trace.is_fallback else 1.0
        if output_expect.state_not and trace.state_value == output_expect.state_not:
            no_fallback = 0.0
        if output_expect.state_in and trace.state_value not in output_expect.state_in:
            no_fallback = 0.0

        # 2. 有实质内容
        has_content = 1.0 if trace.has_content else 0.0

        # 3. 必须包含关键词
        must_contain = output_expect.must_contain
        searchable_text = trace.searchable_text
        if must_contain:
            contain_score = sum(1 for kw in must_contain if kw in searchable_text) / len(must_contain)
        else:
            contain_score = 1.0  # 无关键词要求则满分

        # 4. 不得包含关键词
        must_not_contain = output_expect.must_not_contain
        if must_not_contain:
            violation = sum(1 for kw in must_not_contain if kw in searchable_text)
            not_contain_score = 1.0 if violation == 0 else 0.0
        else:
            not_contain_score = 1.0

        # 5. 推荐类型匹配
        rec_type_match = 1.0
        if output_expect.recommendations_type:
            recs = trace.recommendations
            if recs:
                actual_types = {r.get("type") for r in recs if isinstance(r, dict)}
                rec_type_match = 1.0 if output_expect.recommendations_type in actual_types else 0.0
            else:
                rec_type_match = 0.0

        # 6. 综合任务成功评分
        task_success = (
            no_fallback * 0.35
            + has_content * 0.25
            + contain_score * 0.20
            + not_contain_score * 0.10
            + rec_type_match * 0.10
        )

        return {
            "task_success": task_success,
            "no_fallback": no_fallback,
            "has_content": has_content,
            "must_contain_score": contain_score,
            "must_not_contain_score": not_contain_score,
            "rec_type_match": rec_type_match,
        }

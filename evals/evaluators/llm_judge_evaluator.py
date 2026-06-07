"""LLMJudgeEvaluator — LLM-as-Judge 评测器.

使用另一个 LLM 对开放质量维度（相关性、可执行性、幻觉）进行评分。
支持 rubric 版本化配置，从 evals/configs/rubric.yaml 加载评分标准。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator
from evals.rubric import (
    build_judge_prompt,
    get_dimension_rubric,
    get_rubric_dimensions,
    get_rubric_version,
)

logger = logging.getLogger("evals.llm_judge")

# hallucination_control 维度需要反转分数
_INVERTED_DIMS = {"hallucination", "hallucination_control"}


class LLMJudgeEvaluator(BaseEvaluator):
    """LLM-as-Judge 评测器

    使用 LLM 对回答质量进行评分，覆盖规则难以判定的维度。
    支持从 rubric.yaml 加载版本化的评分标准。
    """

    def __init__(
        self,
        judge_fn: Any | None = None,
        model: str = "qwen-max",
        dimensions: list[str] | None = None,
    ):
        """
        Args:
            judge_fn: 自定义的 LLM 调用函数，签名为 async (prompt: str) -> str
            model: 默认使用的模型名称
            dimensions: 评分维度列表（默认从 rubric.yaml 加载）
        """
        self.judge_fn = judge_fn
        self.model = model
        self.dimensions = dimensions or get_rubric_dimensions()
        self.rubric_version = get_rubric_version()

    @property
    def name(self) -> str:
        return "llm_judge"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        """同步版本的 evaluate（LLM Judge 通常需要异步调用）

        注意：如果 judge_fn 是异步的，请使用 evaluate_async。
        这里提供同步回退，返回默认分数。
        """
        # 同步回退：基于简单规则给出估计分数
        return self._rule_based_fallback(case, trace)

    async def evaluate_async(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        """异步版本的 evaluate"""
        if self.judge_fn is None:
            return self._rule_based_fallback(case, trace)

        prompt = self._build_judge_prompt_from_rubric(case, trace)

        try:
            response = await self.judge_fn(prompt)
            scores = self._parse_scores(response)
            # 记录 judge 元信息到 trace
            trace.judge_scores = {k: v for k, v in scores.items() if k != "llm_judge_overall" and k != "llm_judge_skipped"}
            trace.judge_reasons = {dim: "LLM Judge" for dim in self.dimensions}
            trace.judge_skipped_reason = None
            return scores
        except Exception as exc:
            logger.warning("LLM Judge failed for case %s: %s", case.id, exc)
            return self._rule_based_fallback(case, trace)

    def _build_judge_prompt_from_rubric(self, case: EvalCase, trace: EvalTrace) -> str:
        """从 rubric.yaml 构建 Judge prompt"""
        # 构建推荐摘要
        recommendations = trace.recommendations
        rec_summary = ""
        if recommendations:
            for i, rec in enumerate(recommendations[:5], 1):
                if isinstance(rec, dict):
                    rec_summary += f"  {i}. [{rec.get('type', '?')}] {rec.get('title', '?')}\n"

        tool_calls = trace.tool_call_names
        tool_summary = ", ".join(tool_calls) if tool_calls else "无"

        return build_judge_prompt(
            user_query=case.task,
            scene=trace.scene or "未知",
            tool_calls=tool_summary,
            recovery_events=len(trace.recovery_events),
            recommendations=rec_summary if rec_summary else "无推荐",
            response_text=trace.raw_text or "",
            dimensions=self.dimensions,
        )

    def _parse_scores(self, response: str) -> dict[str, float]:
        """解析 LLM 返回的评分"""
        try:
            # 尝试提取 JSON
            json_str = response.strip()
            # 去掉可能的 markdown 代码块标记
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                json_str = "\n".join(lines[1:-1])

            scores = json.loads(json_str)

            result = {}
            for dim in self.dimensions:
                value = scores.get(dim, 0.5)
                if isinstance(value, (int, float)):
                    # hallucination/hallucination_control 需要反转：0=好，1=坏
                    if dim in _INVERTED_DIMS:
                        result[dim] = 1.0 - min(1.0, max(0.0, float(value)))
                    else:
                        result[dim] = min(1.0, max(0.0, float(value)))
                else:
                    result[dim] = 0.5

            # 综合分
            result["llm_judge_overall"] = sum(result.values()) / len(result) if result else 0.5

            return result

        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to parse LLM Judge response: %s", exc)
            return {dim: 0.5 for dim in self.dimensions}

    def _rule_based_fallback(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        """规则回退：当 LLM Judge 不可用时基于规则给出估计分数"""
        # 相关性估计：检查回答中是否包含用户输入中的关键词
        task_words = set(case.task) if case.task else set()
        raw_text = trace.raw_text
        relevance = 0.5  # 默认中等
        if raw_text and task_words:
            overlap = sum(1 for w in task_words if w in raw_text) / max(len(task_words), 1)
            relevance = min(1.0, 0.3 + overlap * 0.7)

        # 可执行性估计：有推荐且非 fallback
        actionability = 0.5
        if trace.has_content and not trace.is_fallback:
            actionability = 0.7
            # 有地址/价格的推荐更可执行
            for rec in trace.recommendations:
                if isinstance(rec, dict):
                    if rec.get("geo") or rec.get("price"):
                        actionability = 0.9
                        break

        # 幻觉估计：保守给中等分
        hallucination_control = 0.5  # 无法确定，给中等

        # 约束遵守：如果有约束信息则评估
        constraint_adherence = 0.5
        output_expectations = case.expectations.get("output", {}) if isinstance(case.expectations, dict) else {}
        constraints = output_expectations.get("must_satisfy") if isinstance(output_expectations, dict) else None
        if constraints:
            constraint_adherence = 0.6  # 保守估计

        # 工具调用合理性
        tool_call_reasonableness = 0.5
        if trace.tool_call_names and not trace.error:
            tool_call_reasonableness = 0.7

        # 安全合规
        safety_compliance = 1.0  # 默认给满分（安全类有专门的 SafetyEvaluator）

        result = {
            "answer_relevance": relevance,
            "actionability": actionability,
            "hallucination_control": hallucination_control,
            "constraint_adherence": constraint_adherence,
            "tool_call_reasonableness": tool_call_reasonableness,
            "safety_compliance": safety_compliance,
        }

        result["llm_judge_overall"] = sum(result.values()) / len(result)
        return result

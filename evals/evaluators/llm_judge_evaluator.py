"""LLMJudgeEvaluator — LLM-as-Judge 评测器.

使用另一个 LLM 对开放质量维度（相关性、可执行性、幻觉）进行评分。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase
from evals.evaluators.base import BaseEvaluator

logger = logging.getLogger("evals.llm_judge")


class LLMJudgeEvaluator(BaseEvaluator):
    """LLM-as-Judge 评测器

    使用 LLM 对回答质量进行评分，覆盖规则难以判定的维度。
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
            dimensions: 评分维度列表
        """
        self.judge_fn = judge_fn
        self.model = model
        self.dimensions = dimensions or ["relevance", "actionability", "hallucination"]

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

        prompt = self._build_judge_prompt(case, trace)

        try:
            response = await self.judge_fn(prompt)
            return self._parse_scores(response)
        except Exception as exc:
            logger.warning("LLM Judge failed for case %s: %s", case.id, exc)
            return self._rule_based_fallback(case, trace)

    def _build_judge_prompt(self, case: EvalCase, trace: EvalTrace) -> str:
        """构建 Judge prompt"""
        dimensions_desc = {
            "relevance": "回答是否与用户问题相关（0=完全不相关，1=高度相关）",
            "actionability": "推荐是否可执行（地址真实、价格合理、步骤清晰）（0=不可执行，1=完全可执行）",
            "hallucination": "是否存在无证据的断言（0=无幻觉，1=严重幻觉，需反转）",
            "completeness": "回答是否完整覆盖用户需求（0=严重缺失，1=完整覆盖）",
            "coherence": "回答是否连贯有逻辑（0=混乱，1=高度连贯）",
        }

        dim_lines = []
        for dim in self.dimensions:
            desc = dimensions_desc.get(dim, f"{dim}（0-1分）")
            dim_lines.append(f"- {dim}: {desc}")

        final_text = trace.raw_text or ""
        recommendations = trace.recommendations
        rec_summary = ""
        if recommendations:
            for i, rec in enumerate(recommendations[:5], 1):
                if isinstance(rec, dict):
                    rec_summary += f"  {i}. [{rec.get('type', '?')}] {rec.get('title', '?')}\n"

        tool_calls = trace.tool_call_names
        tool_summary = ", ".join(tool_calls) if tool_calls else "无"

        return f"""你是一个公正的评测员。请评估以下 AI 助手的表现。

## 用户请求
{case.task}

## 助手执行信息
- 路由场景: {trace.scene or '未知'}
- 工具调用: {tool_summary}
- 恢复事件数: {len(trace.recovery_events)}

## 推荐内容
{rec_summary if rec_summary else "无推荐"}

## 文本回复（摘要）
{final_text[:1000]}

## 评分维度
{chr(10).join(dim_lines)}

请严格输出 JSON 格式（不要包含其他内容）：
{{{", ".join(f'"{d}": 0.8' for d in self.dimensions)}}}
"""

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
                    # hallucination 需要反转：0=好，1=坏
                    if dim == "hallucination":
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
        hallucination = 0.5  # 无法确定，给中等

        result = {
            "relevance": relevance,
            "actionability": actionability,
            "hallucination": hallucination,
        }

        result["llm_judge_overall"] = sum(result.values()) / len(result)
        return result

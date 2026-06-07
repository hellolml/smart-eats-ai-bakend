"""实时评测监控 — 对话结束后自动采集轨迹并评分.

设计原则:
- 零期望评分：不需要 EvalCase expectations，完全基于 trace 数据
- 异步非阻塞：评分在后台 asyncio task 中执行，不影响 SSE 流
- 采样可控：通过环境变量 REALTIME_EVAL_SAMPLE_RATE 控制采样率
- 复用现有 Evaluator：直接调用 EfficiencyEvaluator / SchemaEvaluator / TaskEvaluator 的部分维度
- 独立存储：使用专用的 realtime_eval 表，与批量评测数据隔离
"""
from __future__ import annotations

import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.common.config import settings
from evals.adapters.trace import EvalTrace, StepTrace
from evals.datasets.eval_case import Category, EvalCase, Scene
from evals.evaluators.efficiency_evaluator import EfficiencyEvaluator
from evals.evaluators.schema_evaluator import SchemaEvaluator
from evals.evaluators.safety_evaluator import LEAK_PATTERNS, REFUSAL_CUES
from evals.evaluators.task_evaluator import TaskEvaluator

logger = logging.getLogger("agent.realtime_eval")

# ---------------------------------------------------------------------------
# 采样控制
# ---------------------------------------------------------------------------

def _get_sample_rate() -> float:
    try:
        raw = os.getenv("REALTIME_EVAL_SAMPLE_RATE")
        rate = float(raw) if raw is not None else float(settings.REALTIME_EVAL_SAMPLE_RATE)
    except (ValueError, TypeError):
        rate = 0.1
    # Clamp to [0, 1]
    return max(0.0, min(1.0, rate))


def should_sample() -> bool:
    """根据采样率决定是否对本次对话执行实时评测."""
    rate = _get_sample_rate()
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    return random.random() < rate


# ---------------------------------------------------------------------------
# 从对话 events 构建 EvalTrace
# ---------------------------------------------------------------------------

def build_trace_from_events(
    *,
    session_id: str,
    events: list[dict[str, Any]],
    final_json: dict[str, Any] | None,
    user_message: str | None = None,
    total_duration_ms: float = 0.0,
) -> EvalTrace:
    """将 run_chat_stream 收集的 SSE events 转为 EvalTrace.

    注意：这里的 events 是完整的 SSE 事件列表（对话结束后重建），
    与 run_chat_stream 中被消费清空的 events 不同。
    """
    trace = EvalTrace(
        run_id=f"realtime-{uuid.uuid4().hex[:8]}",
        case_id=f"session-{session_id}",
        trial_number=0,
    )

    step_index = 0
    for evt in events:
        event_type = evt.get("event", "")
        data = evt.get("data", {})

        if event_type == "context":
            # 提取路由信息
            trace.actual_scene = data.get("scene") or data.get("worker")
            trace.actual_worker = data.get("agent_id") or data.get("worker")
            trace.active_skills = data.get("active_skills") or data.get("skill")
            trace.allowed_tools = data.get("allowed_tools")
            trace.context_budget = data.get("context_budget")
            trace.steps.append(StepTrace(
                step_number=step_index,
                event_type="context",
                raw_data=data,
            ))
            step_index += 1

        elif event_type == "tool_call":
            trace.steps.append(StepTrace(
                step_number=step_index,
                event_type="tool_call",
                tool_name=data.get("name"),
                tool_input=data.get("args"),
                result_preview=data.get("result_preview"),
                duration_ms=data.get("latency_ms") or data.get("duration_ms", 0),
            ))
            step_index += 1

        elif event_type == "tool_result":
            trace.steps.append(StepTrace(
                step_number=step_index,
                event_type="tool_result",
                tool_name=data.get("name"),
                result_preview=data.get("output_preview"),
                raw_data=data,
            ))
            step_index += 1

        elif event_type == "delta":
            trace.first_delta_ms = trace.first_delta_ms or 0.0
            trace.steps.append(StepTrace(
                step_number=step_index,
                event_type="delta",
            ))
            step_index += 1

        elif event_type == "final":
            trace.final_json = data.get("answer") or final_json
            # 兜底：如果 final 事件没有 answer，用传入的 final_json
            if trace.final_json is None:
                trace.final_json = final_json
            # 从 final_json 中再次尝试提取 scene/worker
            if isinstance(trace.final_json, dict):
                if not trace.actual_scene:
                    trace.actual_scene = trace.final_json.get("scene")
                if not trace.actual_worker:
                    trace.actual_worker = trace.final_json.get("agent_id")
            trace.steps.append(StepTrace(
                step_number=step_index,
                event_type="final",
                raw_data=data,
            ))
            step_index += 1

        elif event_type == "error":
            trace.error = str(data.get("message", ""))
            trace.error_reason = str(data.get("code", ""))
            trace.steps.append(StepTrace(
                step_number=step_index,
                event_type="error",
                raw_data=data,
            ))
            step_index += 1

        elif event_type in ("thinking", "paused"):
            trace.steps.append(StepTrace(
                step_number=step_index,
                event_type=event_type,
                raw_data=data,
            ))
            step_index += 1

    # 设置最终 JSON 和耗时
    if trace.final_json is None:
        trace.final_json = final_json
    trace.total_duration_ms = total_duration_ms

    return trace


# ---------------------------------------------------------------------------
# 零期望轻量评分
# ---------------------------------------------------------------------------

@dataclass
class RealtimeEvalResult:
    """实时评测结果."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str = ""
    user_message: str | None = None
    scene: str | None = None
    agent_id: str | None = None
    is_fallback: bool = False
    has_content: bool = False
    total_duration_ms: float = 0.0

    # 评分维度（0-1）
    efficiency: float = 0.0
    schema_compliance: float = 0.0
    no_fallback: float = 0.0
    has_content_score: float = 0.0
    no_leak: float = 1.0
    overall_quality: float = 0.0

    # 工具调用详情
    tool_call_count: int = 0
    repeated_action_rate: float = 0.0
    tool_names: list[str] = field(default_factory=list)

    # 元信息
    error: str | None = None
    error_reason: str | None = None
    created_at: float = field(default_factory=time.time)

    # 原始评分明细
    scores_detail: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_message": self.user_message,
            "scene": self.scene,
            "agent_id": self.agent_id,
            "is_fallback": self.is_fallback,
            "has_content": self.has_content,
            "total_duration_ms": self.total_duration_ms,
            "efficiency": self.efficiency,
            "schema_compliance": self.schema_compliance,
            "no_fallback": self.no_fallback,
            "has_content_score": self.has_content_score,
            "no_leak": self.no_leak,
            "overall_quality": self.overall_quality,
            "tool_call_count": self.tool_call_count,
            "repeated_action_rate": self.repeated_action_rate,
            "tool_names": self.tool_names,
            "error": self.error,
            "error_reason": self.error_reason,
            "created_at": self.created_at,
            "scores_detail": self.scores_detail,
        }


def _make_zero_expect_case(scene: str, user_message: str | None = None) -> EvalCase:
    """构造一个零期望的 EvalCase，用于不需要期望值的评分维度."""
    return EvalCase(
        id="realtime-zero",
        category=Category.NORMAL,
        scene=Scene.CHAT if scene == "chat" else Scene.EAT_OUT,
        task=user_message or "",
        initial_context={},
        expectations={},
        scoring={},
        tags=["realtime"],
        priority="p1",
        difficulty="medium",
    )


def evaluate_realtime(trace: EvalTrace) -> RealtimeEvalResult:
    """对 trace 执行零期望轻量评分.

    评分维度：
    1. efficiency — 效率（步骤数、重复、延迟），完全不需要期望值
    2. schema_compliance — 结构合规，不需要期望值
    3. no_fallback — 非 fallback，不需要期望值
    4. has_content — 有实质内容，不需要期望值
    5. no_leak — 无泄露，不需要期望值（硬编码检查）
    6. overall_quality — 综合质量分（加权）
    """
    scene = trace.actual_scene or "chat"
    case = _make_zero_expect_case(scene, user_message=None)

    result = RealtimeEvalResult(
        session_id=trace.case_id.replace("session-", "") if trace.case_id.startswith("session-") else trace.case_id,
        scene=scene,
        agent_id=trace.actual_worker,
        is_fallback=trace.is_fallback,
        has_content=trace.has_content,
        total_duration_ms=trace.total_duration_ms or 0.0,
        tool_call_count=len(trace.tool_calls),
        tool_names=trace.tool_call_names,
        error=trace.error,
        error_reason=trace.error_reason,
    )

    # 1. 效率评分
    try:
        eff_scores = EfficiencyEvaluator().evaluate(case, trace)
        result.efficiency = eff_scores.get("efficiency", 0.0)
        result.repeated_action_rate = eff_scores.get("repeated_action_rate", 0.0)
        result.scores_detail["efficiency"] = eff_scores
    except Exception:
        logger.warning("realtime_eval efficiency failed", exc_info=True)

    # 2. Schema 合规
    try:
        schema_scores = SchemaEvaluator().evaluate(case, trace)
        result.schema_compliance = schema_scores.get("schema_compliance", 0.0)
        result.scores_detail["schema"] = schema_scores
    except Exception:
        logger.warning("realtime_eval schema failed", exc_info=True)

    # 3. Task 的 no_fallback + has_content（不依赖期望值）
    result.no_fallback = 0.0 if trace.is_fallback else 1.0
    result.has_content_score = 1.0 if trace.has_content else 0.0

    # 4. 安全泄露检查（不需要期望值）
    text = trace.searchable_text
    lower_text = text.lower()
    leaked = any(term and term.lower() in lower_text for term in LEAK_PATTERNS)
    result.no_leak = 0.0 if leaked else 1.0

    # 5. 综合质量分（加权）
    result.overall_quality = (
        result.no_fallback * 0.30
        + result.has_content_score * 0.20
        + result.efficiency * 0.20
        + result.schema_compliance * 0.15
        + result.no_leak * 0.15
    )

    return result

"""EvalTrace — 评测执行轨迹数据结构.

记录一次 Agent 执行的完整 SSE 事件流，供 Evaluator 评分使用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepTrace:
    """单步执行记录"""
    step_number: int
    event_type: str             # "thinking" / "context" / "tool_call" / "tool_result" / "delta" / "final" / "recovery"
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any = None
    result_preview: str | None = None    # 工具返回摘要
    timestamp: float = 0.0
    duration_ms: float = 0.0
    raw_data: dict[str, Any] | None = None  # 原始 SSE 数据


@dataclass
class RecoveryEvent:
    """恢复路径事件"""
    path: str          # 恢复路径名称（如 "clarify", "best_effort_fallback"）
    trigger: str       # 触发恢复的错误类型
    message: str = ""  # 恢复时给用户的消息


@dataclass
class EvalTrace:
    """一次完整执行的轨迹"""
    run_id: str
    case_id: str
    trial_number: int
    steps: list[StepTrace] = field(default_factory=list)
    final_json: dict[str, Any] | None = None
    scene: str | None = None              # 兼容字段：实际 scene（如果采集到）
    actual_scene: str | None = None       # 从运行时事件提取的实际 scene
    expected_scene: str | None = None     # 来自 EvalCase 的期望 scene
    actual_worker: str | None = None      # Supervisor 实际路由到的 worker
    skill: str | None = None              # 兼容字段：激活 skill 摘要
    active_skills: list[str] = field(default_factory=list)
    context_budget: dict[str, Any] | None = None
    allowed_tools: list[str] | None = None
    total_duration_ms: float = 0.0
    first_delta_ms: float | None = None   # 首个 delta 事件的延迟
    started_at_monotonic: float | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    recovery_events: list[RecoveryEvent] = field(default_factory=list)
    error: str | None = None              # 执行过程中的异常信息
    error_reason: str | None = None
    phoenix_trace_url: str | None = None
    judge_scores: dict[str, float] = field(default_factory=dict)
    judge_reasons: dict[str, str] = field(default_factory=dict)
    judge_skipped_reason: str | None = None

    @property
    def tool_calls(self) -> list[StepTrace]:
        """所有工具调用步骤"""
        return [s for s in self.steps if s.event_type == "tool_call"]

    @property
    def tool_call_names(self) -> list[str]:
        """所有工具调用名称列表"""
        return [s.tool_name for s in self.tool_calls if s.tool_name]

    @property
    def unique_tool_names(self) -> list[str]:
        """去重后的工具调用名称"""
        seen = set()
        result = []
        for name in self.tool_call_names:
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    @property
    def state_value(self) -> str | None:
        """从 final_json 提取 state"""
        if isinstance(self.final_json, dict):
            return self.final_json.get("state")
        return None

    @property
    def recommendations(self) -> list[dict[str, Any]]:
        """从 final_json 提取 recommendations"""
        if isinstance(self.final_json, dict):
            recs = self.final_json.get("recommendations")
            if isinstance(recs, list):
                return recs
        return []

    @property
    def raw_text(self) -> str:
        """从 final_json 提取 raw_text"""
        if isinstance(self.final_json, dict):
            return str(self.final_json.get("raw_text") or "")
        return ""

    @property
    def searchable_text(self) -> str:
        """用于规则评测的可搜索文本，覆盖最终文本和推荐字段。"""
        parts = [self.raw_text]
        for rec in self.recommendations:
            if not isinstance(rec, dict):
                continue
            for field in ("title", "reason"):
                value = rec.get(field)
                if value is not None:
                    parts.append(str(value))
            tags = rec.get("tags")
            if isinstance(tags, list):
                parts.extend(str(tag) for tag in tags if tag is not None)
        return "\n".join(part for part in parts if part)

    @property
    def is_fallback(self) -> bool:
        """是否为 fallback 回答"""
        for rec in self.recommendations:
            if isinstance(rec, dict) and str(rec.get("reason") or "") == "fallback":
                return True
        return False

    @property
    def has_content(self) -> bool:
        """是否有实质内容"""
        return len(self.recommendations) > 0

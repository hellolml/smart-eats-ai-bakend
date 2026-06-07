"""EvalCase — 评测用例数据结构定义.

与 AgentState/FinalAnswerArgs 对齐，支持 5 种场景 5 种类别的评测用例。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── 枚举 ──────────────────────────────────────────────────────


class Category(str, Enum):
    """评测用例类别"""
    NORMAL = "normal"
    BOUNDARY = "boundary"
    TOOL_FAILURE = "tool_failure"
    SAFETY = "safety"
    REGRESSION = "regression"


class Scene(str, Enum):
    """Agent 场景，与 AgentState.scene / IntentDecision.intent 对齐"""
    EAT_OUT = "eat_out"
    COOK_HOME = "cook_home"
    ROUTE = "route"
    TRAVEL = "travel_planner"   # 注意：后端 scene 字段为 travel_planner
    CHAT = "chat"


# ── 期望定义 ──────────────────────────────────────────────────


@dataclass
class ToolExpectation:
    """工具调用期望"""
    required: list[str] = field(default_factory=list)   # 必须调用的工具
    forbidden: list[str] = field(default_factory=list)   # 禁止调用的工具
    optional: list[str] = field(default_factory=list)    # 可选调用的工具


@dataclass
class OutputExpectation:
    """输出期望"""
    state_not: str | None = None                          # state 不应是此值（如 "fallback"）
    state_in: list[str] | None = None                     # state 应是其中之一
    recommendations_type: str | None = None               # "restaurant" / "recipe" / "note"
    must_contain: list[str] = field(default_factory=list) # 回答必须包含的关键词
    must_not_contain: list[str] = field(default_factory=list)  # 回答不得包含的关键词
    must_satisfy: dict[str, Any] = field(default_factory=dict) # 约束条件（如 budget_max=100）
    schema_compliant: bool = True                         # 是否要求 JSON Schema 合规


@dataclass
class RecoveryExpectation:
    """恢复路径期望"""
    trigger: str            # 触发恢复的错误类型（如 "empty_result", "geocode_not_found"）
    expected_path: str      # 期望恢复路径（如 "clarify", "best_effort_fallback"）
    expected_state: str     # 恢复后期望的 state 值


# ── 评分权重 ──────────────────────────────────────────────────


@dataclass
class ScoringWeights:
    """评分权重（各值之和应为 1.0）"""
    task_success: float = 0.35
    tool_accuracy: float = 0.20
    intent_accuracy: float = 0.15
    constraint_satisfaction: float = 0.15
    schema_compliance: float = 0.10
    efficiency: float = 0.05


# 不同类别用例的默认权重
DEFAULT_SCORING_BY_CATEGORY: dict[str, dict[str, float]] = {
    Category.NORMAL: {
        "task_success": 0.35,
        "tool_accuracy": 0.20,
        "intent_accuracy": 0.15,
        "constraint_satisfaction": 0.15,
        "schema_compliance": 0.10,
        "efficiency": 0.05,
    },
    Category.BOUNDARY: {
        "task_success": 0.25,
        "tool_accuracy": 0.15,
        "intent_accuracy": 0.15,
        "constraint_satisfaction": 0.15,
        "schema_compliance": 0.10,
        "recovery_score": 0.10,
        "efficiency": 0.10,
    },
    Category.TOOL_FAILURE: {
        "recovery_score": 0.40,
        "tool_accuracy": 0.25,
        "task_success": 0.20,
        "schema_compliance": 0.15,
    },
    Category.SAFETY: {
        "safety_score": 0.60,
        "no_leak": 0.30,
        "graceful_reject": 0.10,
    },
    Category.REGRESSION: {
        "task_success": 0.35,
        "tool_accuracy": 0.20,
        "intent_accuracy": 0.15,
        "constraint_satisfaction": 0.15,
        "schema_compliance": 0.10,
        "efficiency": 0.05,
    },
}


class _EvalCaseModel(BaseModel):
    """Pydantic loader schema for dataset validation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    scene: Scene
    task: str
    initial_context: dict[str, Any] = Field(default_factory=dict)
    expectations: dict[str, Any] = Field(default_factory=dict)
    scoring: dict[str, float] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    priority: str = "p1"
    difficulty: str = "medium"


# ── 主数据结构 ────────────────────────────────────────────────


@dataclass
class EvalCase:
    """一条完整的评测用例"""
    id: str
    category: Category
    scene: Scene
    task: str                                      # 用户输入
    initial_context: dict[str, Any] = field(default_factory=dict)  # 初始上下文
    expectations: dict[str, Any] = field(default_factory=dict)     # 期望（含 intent/tools/output/recovery）
    scoring: dict[str, float] = field(default_factory=dict)        # 评分权重（空则用默认）
    tags: list[str] = field(default_factory=list)
    priority: str = "p1"
    difficulty: str = "medium"                     # easy / medium / hard

    def get_scoring(self) -> dict[str, float]:
        """获取评分权重，未指定时按类别使用默认值"""
        if self.scoring:
            return self.scoring
        return DEFAULT_SCORING_BY_CATEGORY.get(self.category, DEFAULT_SCORING_BY_CATEGORY[Category.NORMAL])

    def get_tool_expectation(self) -> ToolExpectation:
        """从 expectations 中解析工具期望"""
        tools = self.expectations.get("tools", {})
        return ToolExpectation(
            required=tools.get("required", []),
            forbidden=tools.get("forbidden", []),
            optional=tools.get("optional", []),
        )

    def get_output_expectation(self) -> OutputExpectation:
        """从 expectations 中解析输出期望"""
        output = self.expectations.get("output", {})
        return OutputExpectation(
            state_not=output.get("state_not"),
            state_in=output.get("state_in"),
            recommendations_type=output.get("recommendations_type"),
            must_contain=output.get("must_contain", []),
            must_not_contain=output.get("must_not_contain", []),
            must_satisfy=output.get("must_satisfy", {}),
            schema_compliant=output.get("schema_compliant", True),
        )

    def get_recovery_expectation(self) -> RecoveryExpectation | None:
        """从 expectations 中解析恢复期望"""
        recovery = self.expectations.get("recovery")
        if not recovery:
            return None
        return RecoveryExpectation(
            trigger=recovery["trigger"],
            expected_path=recovery["expected_path"],
            expected_state=recovery["expected_state"],
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        import dataclasses
        result = {}
        for f in dataclasses.fields(self):
            val = getattr(self, f.name)
            if isinstance(val, Enum):
                val = val.value
            elif isinstance(val, list) and val and isinstance(val[0], Enum):
                val = [v.value for v in val]
            result[f.name] = val
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalCase:
        """从字典反序列化"""
        model = _EvalCaseModel.model_validate(data)
        return cls(
            id=model.id,
            category=model.category,
            scene=model.scene,
            task=model.task,
            initial_context=model.initial_context,
            expectations=model.expectations,
            scoring=model.scoring,
            tags=model.tags,
            priority=model.priority,
            difficulty=model.difficulty,
        )

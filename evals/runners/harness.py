"""EvalHarness — 评测总控.

协调 TrialRunner、Evaluators 和 Reporters，执行完整评测流程。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import Category, EvalCase
from evals.evaluators.base import BaseEvaluator
from evals.evaluators.constraint_evaluator import ConstraintEvaluator
from evals.evaluators.deepeval_judge_evaluator import DeepEvalJudgeEvaluator
from evals.evaluators.efficiency_evaluator import EfficiencyEvaluator
from evals.evaluators.intent_evaluator import IntentEvaluator
from evals.evaluators.recovery_evaluator import RecoveryEvaluator
from evals.evaluators.safety_evaluator import SafetyEvaluator
from evals.evaluators.schema_evaluator import SchemaEvaluator
from evals.evaluators.task_evaluator import TaskEvaluator
from evals.evaluators.tool_evaluator import ToolEvaluator
from evals.evaluators.travel_state_evaluator import TravelStateEvaluator
from evals.observability.phoenix import PhoenixTracer
from evals.runners.trial_runner import TrialRunner

logger = logging.getLogger("evals.harness")


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class TrialResult:
    """单次试验结果"""
    case_id: str
    trial_number: int
    trace: EvalTrace
    scores: dict[str, float] = field(default_factory=dict)
    weighted_score: float = 0.0
    threshold_failures: list[dict[str, Any]] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)


@dataclass
class TaskResult:
    """一个用例的多次试验汇总"""
    case: EvalCase
    trials: list[TrialResult] = field(default_factory=list)

    @property
    def avg_scores(self) -> dict[str, float]:
        """各指标的平均分"""
        if not self.trials:
            return {}
        all_keys: set[str] = set()
        for t in self.trials:
            all_keys.update(t.scores.keys())

        avg: dict[str, float] = {}
        for key in all_keys:
            values = [t.scores.get(key, 0.0) for t in self.trials]
            avg[key] = sum(values) / len(values)
        return avg

    @property
    def success_rate(self) -> float:
        """任务成功率（加权分数 >= 0.5 视为成功）"""
        if not self.trials:
            return 0.0
        successes = sum(1 for t in self.trials if t.weighted_score >= 0.5)
        return successes / len(self.trials)


@dataclass
class EvalReport:
    """完整评测报告"""
    results: list[TaskResult] = field(default_factory=list)
    total_cases: int = 0
    total_trials: int = 0
    overall_success_rate: float = 0.0
    category_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)
    scene_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)
    failure_summary: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    duration_seconds: float = 0.0


# ── 配置 ──────────────────────────────────────────────────────


@dataclass
class HarnessConfig:
    """Harness 配置"""
    base_url: str = "http://127.0.0.1:8000"
    timeout: float = 60.0
    num_trials: int = 3
    output_dir: str = "./eval_results"
    dataset_dir: str = "./evals/datasets"
    suite: str = "full"
    runner: str = "live"
    fixture_path: str = "./evals/datasets/fixture_traces.json"
    include_llm_judge: bool = False
    thresholds: dict[str, float] = field(default_factory=lambda: {
        "task_success": 0.80,
        "intent_accuracy": 0.90,
        "tool_accuracy": 0.85,
        "recovery_score": 0.70,
        "schema_compliance": 0.95,
        "safety_score": 0.95,
        "no_leak": 0.99,
        "p0_success_rate": 1.0,
        "category:safety:safety_score": 0.95,
        "category:safety:no_leak": 0.99,
    })


# ── Harness ───────────────────────────────────────────────────


class EvalHarness:
    """评测总控"""

    def __init__(self, config: HarnessConfig | None = None):
        self.config = config or HarnessConfig()
        self.evaluators: list[BaseEvaluator] = self._init_evaluators()
        self.runner = TrialRunner(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            runner_type=self.config.runner,
            fixture_path=self.config.fixture_path,
        )
        self.phoenix = PhoenixTracer()

    def _init_evaluators(self) -> list[BaseEvaluator]:
        """初始化评测器列表"""
        evaluators: list[BaseEvaluator] = [
            IntentEvaluator(),
            ToolEvaluator(),
            TaskEvaluator(),
            ConstraintEvaluator(),
            SchemaEvaluator(),
            RecoveryEvaluator(),
            EfficiencyEvaluator(),
            SafetyEvaluator(),
            TravelStateEvaluator(),
        ]
        if self.config.include_llm_judge:
            evaluators.append(DeepEvalJudgeEvaluator())
        return evaluators

    async def run(
        self,
        cases: list[EvalCase] | None = None,
        case_ids: list[str] | None = None,
        categories: list[str] | None = None,
        scenes: list[str] | None = None,
    ) -> EvalReport:
        """运行完整评测

        Args:
            cases: 手动指定的用例列表（优先）
            case_ids: 按 ID 筛选
            categories: 按类别筛选
            scenes: 按场景筛选
        """
        start_time = time.monotonic()

        # 加载用例
        if cases is None:
            cases = self._load_cases(suite=self.config.suite)

        # 筛选
        if case_ids:
            cases = [c for c in cases if c.id in case_ids]
        if categories:
            cases = [c for c in cases if c.category.value in categories]
        if scenes:
            cases = [c for c in cases if c.scene.value in scenes]

        logger.info("Starting evaluation: %d cases, %d trials each", len(cases), self.config.num_trials)

        all_results: list[TaskResult] = []

        for i, case in enumerate(cases):
            logger.info(
                "[%d/%d] Evaluating: %s [%s/%s]",
                i + 1, len(cases), case.id, case.category.value, case.scene.value,
            )

            task_result = TaskResult(case=case)

            for trial_num in range(self.config.num_trials):
                with self.phoenix.trial_span(
                    "agent_eval_trial",
                    {
                        "case_id": case.id,
                        "trial_number": trial_num,
                        "scene_expected": case.scene.value,
                        "category": case.category.value,
                    },
                ) as span:
                    # 执行试验
                    trace = await self.runner.run_trial(case, trial_num)

                    # 评分
                    scores = self._evaluate_case(case, trace)

                    # 计算加权分数
                    weights = case.get_scoring()
                    missing_metrics = [metric for metric in weights if metric not in scores]
                    weighted = self._compute_weighted_score(scores, weights)
                    self.phoenix.set_attributes(
                        span,
                        {
                            "scene_actual": trace.actual_scene or trace.scene,
                            "worker_actual": trace.actual_worker,
                            "active_skills": trace.active_skills,
                            "tool_calls": trace.tool_call_names,
                            "recovery_events": [e.path for e in trace.recovery_events],
                            "final_state": trace.state_value,
                            "duration_ms": trace.total_duration_ms,
                            "first_delta_ms": trace.first_delta_ms,
                            "weighted_score": weighted,
                            "trace_error": trace.error,
                            "trace_error_reason": trace.error_reason,
                        },
                    )
                    trace.phoenix_trace_url = self.phoenix.span_reference(span)

                trial_result = TrialResult(
                    case_id=case.id,
                    trial_number=trial_num,
                    trace=trace,
                    scores=scores,
                    weighted_score=weighted,
                    missing_metrics=missing_metrics,
                    threshold_failures=self._trial_threshold_failures(case, scores, weighted),
                )
                task_result.trials.append(trial_result)

                logger.info(
                    "  Trial %d: weighted=%.3f | top_scores=%s",
                    trial_num, weighted,
                    {k: f"{v:.2f}" for k, v in list(scores.items())[:5]},
                )

            all_results.append(task_result)

        # 生成报告
        report = self._generate_report(all_results)
        report.duration_seconds = time.monotonic() - start_time

        logger.info(
            "Evaluation complete: %d cases, success_rate=%.1f%%, duration=%.1fs",
            report.total_cases, report.overall_success_rate * 100, report.duration_seconds,
        )

        return report

    def _evaluate_case(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        """对一次试验执行所有评测器"""
        all_scores: dict[str, float] = {}

        for evaluator in self.evaluators:
            try:
                scores = evaluator.evaluate(case, trace)
                # 用评测器名称作为前缀，避免键冲突
                for key, value in scores.items():
                    prefixed_key = f"{evaluator.name}.{key}"
                    all_scores[prefixed_key] = value
                    # 也保留不带前缀的版本（后写入覆盖）
                    all_scores[key] = value
            except Exception as exc:
                logger.warning("Evaluator %s failed for case %s: %s", evaluator.name, case.id, exc)

        return all_scores

    def _trial_threshold_failures(
        self,
        case: EvalCase,
        scores: dict[str, float],
        weighted_score: float,
    ) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for metric, threshold in self.config.thresholds.items():
            if metric.startswith(("category:", "scene:")) or metric == "p0_success_rate":
                continue
            value = scores.get(metric)
            if value is not None and value < threshold:
                failures.append({"metric": metric, "actual": value, "threshold": threshold})

        p0_actual = 1.0 if weighted_score >= 0.5 else 0.0
        if case.priority == "p0" and p0_actual < self.config.thresholds.get("p0_success_rate", 1.0):
            failures.append({
                "metric": "p0_success_rate",
                "actual": p0_actual,
                "threshold": self.config.thresholds.get("p0_success_rate", 1.0),
            })

        for metric, threshold in self.config.thresholds.items():
            parts = metric.split(":")
            if len(parts) != 3 or parts[0] not in {"category", "scene"}:
                continue
            scope_type, scope_name, score_key = parts
            if scope_type == "category" and case.category.value != scope_name:
                continue
            if scope_type == "scene" and case.scene.value != scope_name:
                continue
            value = scores.get(score_key)
            if value is not None and value < threshold:
                failures.append({"metric": metric, "actual": value, "threshold": threshold})
        return failures

    def _compute_weighted_score(self, scores: dict[str, float], weights: dict[str, float]) -> float:
        """计算加权总分"""
        total_weight = sum(weights.values())
        if total_weight == 0:
            return 0.0

        missing = [metric for metric in weights if metric not in scores]
        if missing:
            raise ValueError(f"Missing weighted metrics: {', '.join(sorted(missing))}")

        weighted_sum = 0.0
        for metric, weight in weights.items():
            value = scores[metric]
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Weighted metric {metric} out of range: {value}")
            weighted_sum += value * weight

        return weighted_sum / total_weight

    def _load_cases(self, suite: str | None = None) -> list[EvalCase]:
        """按 suite 从数据集目录加载用例."""
        cases: list[EvalCase] = []
        dataset_dir = Path(self.config.dataset_dir)
        suite_name = suite or self.config.suite

        dataset_files = self._dataset_files_for_suite(dataset_dir, suite_name)

        for dataset_file in dataset_files:
            try:
                cases.extend(self._load_case_file(dataset_file))
            except Exception as exc:
                raise ValueError(f"Failed to load dataset {dataset_file}: {exc}") from exc

        if suite_name == "quick":
            cases = [c for c in cases if c.priority in {"p0", "p1"}]
            if self.config.runner == "fixture":
                fixture_ids = self._fixture_case_ids()
                cases = [c for c in cases if c.id in fixture_ids]
        elif suite_name == "live-smoke":
            preferred_ids = {"food-001", "chef-001", "route-001", "travel-001", "chat-001"}
            selected = [c for c in cases if c.id in preferred_ids]
            cases = selected[:5] if selected else cases[:5]

        logger.info("Loaded %d eval cases from %s suite=%s", len(cases), dataset_dir, suite_name)
        return cases

    def _dataset_files_for_suite(self, dataset_dir: Path, suite: str) -> list[Path]:
        if suite == "quick":
            return [dataset_dir / "fixture_cases.json"]
        if suite == "full":
            return [dataset_dir / "full_cases.json", dataset_dir / "golden_cases.jsonl"]
        if suite == "live-smoke":
            return [dataset_dir / "full_cases.json", dataset_dir / "golden_cases.jsonl"]
        raise ValueError(f"Unsupported eval suite: {suite}")

    def _load_case_file(self, path: Path) -> list[EvalCase]:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return [EvalCase.from_dict(item) for item in data]
            if isinstance(data, dict):
                if isinstance(data.get("cases"), list):
                    return [EvalCase.from_dict(item) for item in data["cases"]]
                return [EvalCase.from_dict(data)]
        except json.JSONDecodeError:
            pass
        cases = []
        for line in content.splitlines():
            if line.strip():
                cases.append(EvalCase.from_dict(json.loads(line)))
        return cases

    def _fixture_case_ids(self) -> set[str]:
        try:
            data = json.loads(Path(self.config.fixture_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        items = data.get("traces", []) if isinstance(data, dict) else data
        return {str(item.get("case_id")) for item in items if item.get("case_id")}

    def _generate_report(self, results: list[TaskResult]) -> EvalReport:
        """生成评测报告"""
        report = EvalReport(
            results=results,
            total_cases=len(results),
            total_trials=sum(len(r.trials) for r in results),
        )

        # 总体成功率
        if results:
            report.overall_success_rate = sum(r.success_rate for r in results) / len(results)

        # 按类别分析
        by_category: dict[str, list[TaskResult]] = {}
        for r in results:
            cat = r.case.category.value
            by_category.setdefault(cat, []).append(r)

        for cat, cat_results in by_category.items():
            avg_success = sum(r.success_rate for r in cat_results) / len(cat_results)
            avg_scores: dict[str, float] = {}
            for r in cat_results:
                for key, value in r.avg_scores.items():
                    avg_scores.setdefault(key, 0.0)
                    avg_scores[key] += value / len(cat_results)
            report.category_breakdown[cat] = {
                "success_rate": avg_success,
                **{k: v for k, v in avg_scores.items() if not k.startswith(("intent.", "tool.", "task.", "schema.", "recovery.", "travel_state."))},
            }

        # 按场景分析
        by_scene: dict[str, list[TaskResult]] = {}
        for r in results:
            scene = r.case.scene.value
            by_scene.setdefault(scene, []).append(r)

        for scene, scene_results in by_scene.items():
            avg_success = sum(r.success_rate for r in scene_results) / len(scene_results)
            report.scene_breakdown[scene] = {"success_rate": avg_success}

        report.failure_summary = self._build_failure_summary(results)

        # 时间戳
        from datetime import datetime
        report.timestamp = datetime.now().isoformat()

        return report

    def _build_failure_summary(self, results: list[TaskResult]) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "by_error_reason": {},
            "by_case": {},
            "by_metric": {},
            "by_scene": {},
            "by_category": {},
            "by_tool": {},
            "by_worker": {},
            "by_failure_class": {},
        }
        for task_result in results:
            case = task_result.case
            case_failed = False
            for trial in task_result.trials:
                trial_failed = trial.weighted_score < 0.5
                if trial_failed:
                    case_failed = True
                failure_class = self._classify_failure(
                    trial.trace.error_reason,
                    trial.trace.error,
                    trial.threshold_failures,
                )
                if failure_class != "none":
                    summary["by_failure_class"][failure_class] = summary["by_failure_class"].get(failure_class, 0) + 1
                if trial.trace.error_reason:
                    reason = trial.trace.error_reason
                    summary["by_error_reason"][reason] = summary["by_error_reason"].get(reason, 0) + 1
                if trial.trace.actual_worker and (trial_failed or failure_class != "none"):
                    worker = trial.trace.actual_worker
                    summary["by_worker"][worker] = summary["by_worker"].get(worker, 0) + 1
                for tool_name in trial.trace.tool_call_names:
                    if trial_failed or failure_class != "none":
                        summary["by_tool"][tool_name] = summary["by_tool"].get(tool_name, 0) + 1
                for failure in trial.threshold_failures:
                    metric = failure.get("metric")
                    if metric:
                        summary["by_metric"][metric] = summary["by_metric"].get(metric, 0) + 1
            if case_failed or task_result.success_rate < 1.0:
                summary["by_case"][case.id] = {
                    "success_rate": task_result.success_rate,
                    "category": case.category.value,
                    "scene": case.scene.value,
                }
                summary["by_scene"][case.scene.value] = summary["by_scene"].get(case.scene.value, 0) + 1
                summary["by_category"][case.category.value] = summary["by_category"].get(case.category.value, 0) + 1
        return summary

    def _classify_failure(
        self,
        error_reason: str | None,
        error: str | None,
        threshold_failures: list[dict[str, Any]],
    ) -> str:
        text = " ".join(str(part) for part in (error_reason, error) if part).lower()
        if not text and not threshold_failures:
            return "none"
        if any(token in text for token in ("provider", "api key", "unauthorized", "connection", "connect", "model", "timeout")):
            return "provider"
        if any(token in text for token in ("tool", "amap", "map", "http 4", "http 5", "not found")):
            return "tool_api"
        if any(token in text for token in ("evaluator", "missing weighted metrics", "schema", "eval")):
            return "eval_framework"
        return "agent_quality"

"""Reporters — 评测报告生成器.

支持终端输出、JSON 文件和 HTML 可视化报告。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from evals.runners.harness import EvalReport

logger = logging.getLogger("evals.reporters")


class ConsoleReporter:
    """终端报告输出"""

    def report(self, report: EvalReport) -> None:
        print("\n" + "=" * 70)
        print("📊 Smart-Eats-AI 评测报告")
        print("=" * 70)
        print(f"总用例数: {report.total_cases}")
        print(f"总试验数: {report.total_trials}")
        print(f"总体成功率: {report.overall_success_rate:.1%}")
        print(f"耗时: {report.duration_seconds:.1f}s")
        print(f"时间: {report.timestamp}")
        print("-" * 70)

        # 按类别
        if report.category_breakdown:
            print("\n📋 按类别分析:")
            for cat, data in report.category_breakdown.items():
                print(f"  {cat}: 成功率={data.get('success_rate', 0):.1%}")

        # 按场景
        if report.scene_breakdown:
            print("\n🎭 按场景分析:")
            for scene, data in report.scene_breakdown.items():
                print(f"  {scene}: 成功率={data.get('success_rate', 0):.1%}")

        # 逐用例
        print("\n📝 逐用例结果:")
        for task_result in report.results:
            case = task_result.case
            avg = task_result.avg_scores
            top_metrics = {
                k: f"{v:.2f}"
                for k, v in avg.items()
                if not k.startswith(("intent.", "tool.", "task.", "schema.", "recovery.", "travel_state."))
            }
            print(
                f"  [{case.category.value}/{case.scene.value}] {case.id}: "
                f"成功率={task_result.success_rate:.1%} | {top_metrics}"
            )

        if report.failure_summary:
            print("\n失败聚合:")
            for key, value in report.failure_summary.items():
                if value:
                    print(f"  {key}: {value}")

        print("=" * 70)


class JsonReporter:
    """JSON 文件报告"""

    def __init__(self, output_dir: str = "./eval_results", metadata: dict[str, Any] | None = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.metadata = metadata or {}

    def report(self, report: EvalReport) -> Path:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"eval_report_{timestamp}.json"

        data = self._serialize_report(report)

        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # 同时写一个 latest.json 方便 CI 读取
        latest_path = self.output_dir / "latest.json"
        latest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        logger.info("Report saved to: %s", output_path)
        return output_path

    def _serialize_report(self, report: EvalReport) -> dict[str, Any]:
        """将报告序列化为可 JSON 化的字典"""
        results = []
        for task_result in report.results:
            trials = []
            for t in task_result.trials:
                failure_class = self._classify_failure(t.trace.error_reason, t.trace.error, t.threshold_failures)
                trial_data = {
                    "case_id": t.case_id,
                    "trial_number": t.trial_number,
                    "scores": t.scores,
                    "weighted_score": t.weighted_score,
                    "error": t.trace.error,
                    "tool_calls": t.trace.tool_call_names,
                    "expected_scene": t.trace.expected_scene or task_result.case.scene.value,
                    "actual_scene": t.trace.actual_scene or t.trace.scene,
                    "expected_worker": task_result.case.expectations.get("worker"),
                    "actual_worker": t.trace.actual_worker,
                    "active_skills": t.trace.active_skills,
                    "is_fallback": t.trace.is_fallback,
                    "has_content": t.trace.has_content,
                    "duration_ms": t.trace.total_duration_ms,
                    "first_delta_ms": t.trace.first_delta_ms,
                    "error_reason": t.trace.error_reason,
                    "missing_metrics": t.missing_metrics,
                    "threshold_failures": t.threshold_failures,
                    "outcome_scores": t.outcome_scores,
                    "outcome_failures": t.outcome_failures,
                    "side_effect_failures": t.side_effect_failures,
                    "outcome_details": t.outcome_details,
                    "phoenix_trace_url": t.trace.phoenix_trace_url,
                    "judge_scores": t.trace.judge_scores,
                    "judge_reasons": t.trace.judge_reasons,
                    "llm_judge_skipped_reason": t.trace.judge_skipped_reason,
                    "failure_class": failure_class,
                    "final_answer_preview": self._final_answer_preview(t.trace.final_json, t.trace.raw_text),
                    "trace_timeline": self._trace_timeline(t.trace.steps),
                }
                trials.append(trial_data)

            results.append({
                "case_id": task_result.case.id,
                "category": task_result.case.category.value,
                "scene": task_result.case.scene.value,
                "task": task_result.case.task,
                "priority": task_result.case.priority,
                "success_rate": task_result.success_rate,
                "avg_scores": task_result.avg_scores,
                "trials": trials,
            })

        return {
            "metadata": self._metadata(),
            "timestamp": report.timestamp,
            "total_cases": report.total_cases,
            "total_trials": report.total_trials,
            "overall_success_rate": report.overall_success_rate,
            "category_breakdown": report.category_breakdown,
            "scene_breakdown": report.scene_breakdown,
            "failure_summary": report.failure_summary,
            "stability": self._stability_summary(results),
            "duration_seconds": report.duration_seconds,
            "results": results,
        }

    def _stability_summary(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate pass@k/pass^k style stability metrics from serialized trials."""
        if not results:
            return {
                "k": 0,
                "pass_at_k": 0.0,
                "pass_all_k": 0.0,
                "trial_variance": 0.0,
                "flaky_cases": [],
                "cases": [],
            }

        case_rows: list[dict[str, Any]] = []
        flaky_cases: list[dict[str, Any]] = []
        pass_at_count = 0
        pass_all_count = 0
        variances: list[float] = []
        max_k = 0

        for result in results:
            trials = result.get("trials") if isinstance(result.get("trials"), list) else []
            scores = [float(t.get("weighted_score") or 0.0) for t in trials if isinstance(t, dict)]
            passes = [
                self._trial_passed(t)
                for t in trials
                if isinstance(t, dict)
            ]
            max_k = max(max_k, len(trials))
            passed_once = any(passes) if passes else False
            passed_all = all(passes) if passes else False
            if passed_once:
                pass_at_count += 1
            if passed_all:
                pass_all_count += 1
            variance = self._variance(scores)
            variances.append(variance)
            is_flaky = bool(passes) and (any(passes) != all(passes) or variance >= 0.05)
            row = {
                "case_id": result.get("case_id"),
                "trials": len(trials),
                "pass_count": sum(1 for value in passes if value),
                "pass_at_k": passed_once,
                "pass_all_k": passed_all,
                "scores": scores,
                "variance": variance,
                "flaky": is_flaky,
            }
            case_rows.append(row)
            if is_flaky:
                flaky_cases.append(row)

        total = len(results)
        return {
            "k": max_k,
            "pass_at_k": round(pass_at_count / total, 4) if total else 0.0,
            "pass_all_k": round(pass_all_count / total, 4) if total else 0.0,
            "trial_variance": round(sum(variances) / len(variances), 6) if variances else 0.0,
            "flaky_cases": flaky_cases,
            "cases": case_rows,
        }

    def _trial_passed(self, trial: dict[str, Any]) -> bool:
        if trial.get("error") or trial.get("error_reason"):
            return False
        if trial.get("threshold_failures") or trial.get("missing_metrics"):
            return False
        return float(trial.get("weighted_score") or 0.0) >= 0.7

    def _variance(self, values: list[float]) -> float:
        if len(values) <= 1:
            return 0.0
        mean = sum(values) / len(values)
        return round(sum((value - mean) ** 2 for value in values) / len(values), 6)

    def _metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "suite": self.metadata.get("suite"),
            "runner": self.metadata.get("runner"),
            "base_url": self.metadata.get("base_url"),
            "commit_sha": self.metadata.get("commit_sha") or os.getenv("GITHUB_SHA") or self._git_value(["rev-parse", "HEAD"]),
            "branch": self.metadata.get("branch") or os.getenv("GITHUB_REF_NAME") or self._git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "model_provider": self.metadata.get("model_provider") or os.getenv("LLM_PROVIDER"),
            "model_name": self.metadata.get("model_name") or os.getenv("OPENAI_MODEL_PLANNER") or os.getenv("QWEN_MODEL_PLANNER"),
            "include_llm_judge": bool(self.metadata.get("include_llm_judge", False)),
            "outcome_verify": bool(self.metadata.get("outcome_verify", False)),
            "dataset_version": self.metadata.get("dataset_version"),
            "report_schema_version": "1.2",
        }
        return data

    def _git_value(self, args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=Path(__file__).resolve().parents[2],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            return None
        value = result.stdout.strip()
        return value or None

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

    def _final_answer_preview(self, final_json: dict[str, Any] | None, raw_text: str) -> str:
        if raw_text:
            return raw_text[:500]
        if not isinstance(final_json, dict):
            return ""
        for key in ("raw_text", "message", "answer", "text"):
            value = final_json.get(key)
            if value:
                return str(value)[:500]
        recommendations = final_json.get("recommendations")
        if isinstance(recommendations, list) and recommendations:
            return json.dumps(recommendations[:2], ensure_ascii=False, default=str)[:500]
        return json.dumps(final_json, ensure_ascii=False, default=str)[:500]

    def _trace_timeline(self, steps: list[Any]) -> list[dict[str, Any]]:
        timeline = []
        for index, step in enumerate(steps):
            raw_data = step.raw_data if isinstance(step.raw_data, dict) else {}
            timeline.append({
                "index": index,
                "event_type": step.event_type,
                "timestamp": step.timestamp,
                "label": self._step_label(step.event_type, step.tool_name, raw_data),
                "tool_name": step.tool_name,
                "duration_ms": step.duration_ms,
                "data": raw_data,
            })
        return timeline

    def _step_label(self, event_type: str, tool_name: str | None, data: dict[str, Any]) -> str:
        if event_type == "context":
            worker = data.get("worker") or data.get("actual_worker") or data.get("agent_id")
            scene = data.get("scene") or data.get("actual_scene")
            return f"路由到 {worker or scene or 'agent'}"
        if event_type == "tool_call":
            return f"调用工具 {tool_name or data.get('name') or 'unknown'}"
        if event_type == "tool_result":
            return f"工具返回 {tool_name or data.get('name') or 'unknown'}"
        if event_type == "recovery":
            return f"恢复路径 {data.get('path') or data.get('trigger') or 'recovery'}"
        if event_type == "final":
            return "生成最终回答"
        if event_type == "error":
            return f"错误 {data.get('code') or data.get('message') or 'error'}"
        if event_type == "thinking":
            return "开始思考"
        return event_type


class HtmlReporter:
    """HTML 可视化报告"""

    def __init__(self, output_dir: str = "./eval_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def report(self, report: EvalReport) -> Path:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"eval_report_{timestamp}.html"

        html = self._generate_html(report)
        output_path.write_text(html, encoding="utf-8")

        logger.info("HTML report saved to: %s", output_path)
        return output_path

    def _generate_html(self, report: EvalReport) -> str:
        """生成 HTML 报告"""
        rows = ""
        failure_rows = ""
        detail_rows = ""
        for task_result in report.results:
            case = task_result.case
            avg = task_result.avg_scores
            score_display = " | ".join(
                f"{k}: {v:.2f}" for k, v in list(avg.items())[:8]
            )
            success_class = "success" if task_result.success_rate >= 0.7 else "warning" if task_result.success_rate >= 0.4 else "danger"
            rows += f"""
            <tr>
                <td>{case.id}</td>
                <td>{case.category.value}</td>
                <td>{case.scene.value}</td>
                <td class="{success_class}">{task_result.success_rate:.1%}</td>
                <td title="{score_display}">{task_result.avg_scores.get('weighted_score', task_result.avg_scores.get('task_success', 0)):.2f}</td>
            </tr>"""
            for trial in task_result.trials:
                actual_scene = trial.trace.actual_scene or trial.trace.scene or ""
                tools = ", ".join(trial.trace.tool_call_names)
                detail_rows += f"""
                <tr>
                    <td>{case.id}</td>
                    <td>{case.scene.value}</td>
                    <td>{actual_scene}</td>
                    <td>{case.expectations.get('worker') or ''}</td>
                    <td>{trial.trace.actual_worker or ''}</td>
                    <td>{tools}</td>
                    <td>{trial.trace.error_reason or ''}</td>
                </tr>"""

        for key, value in report.failure_summary.items():
            if value:
                failure_rows += f"<tr><td>{key}</td><td><pre>{json.dumps(value, ensure_ascii=False, indent=2)}</pre></td></tr>"

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Smart-Eats-AI 评测报告</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; margin: 40px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        .summary {{ display: flex; gap: 20px; margin: 20px 0; }}
        .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); flex: 1; text-align: center; }}
        .card h3 {{ margin: 0; color: #666; font-size: 14px; }}
        .card .value {{ font-size: 32px; font-weight: bold; color: #333; margin-top: 8px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; font-weight: 600; }}
        .success {{ color: #28a745; font-weight: bold; }}
        .warning {{ color: #ffc107; font-weight: bold; }}
        .danger {{ color: #dc3545; font-weight: bold; }}
    </style>
</head>
<body>
    <h1>📊 Smart-Eats-AI 评测报告</h1>
    <p>生成时间: {report.timestamp} | 耗时: {report.duration_seconds:.1f}s</p>

    <div class="summary">
        <div class="card"><h3>总用例</h3><div class="value">{report.total_cases}</div></div>
        <div class="card"><h3>总试验</h3><div class="value">{report.total_trials}</div></div>
        <div class="card"><h3>总体成功率</h3><div class="value">{report.overall_success_rate:.1%}</div></div>
    </div>

    <h2>逐用例结果</h2>
    <table>
        <tr><th>ID</th><th>类别</th><th>场景</th><th>成功率</th><th>综合分</th></tr>
        {rows}
    </table>

    <h2>失败原因聚合</h2>
    <table>
        <tr><th>维度</th><th>内容</th></tr>
        {failure_rows or '<tr><td colspan="2">无失败聚合</td></tr>'}
    </table>

    <h2>Trial 对比</h2>
    <table>
        <tr><th>Case</th><th>Expected Scene</th><th>Actual Scene</th><th>Expected Worker</th><th>Actual Worker</th><th>Tools</th><th>Error</th></tr>
        {detail_rows}
    </table>
</body>
</html>"""

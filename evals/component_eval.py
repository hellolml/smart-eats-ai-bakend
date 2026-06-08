"""Component-level evaluation helpers for Smart-Eats AgentEval Hub."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_component_report(component: str, dataset: str, owner: str | None = None) -> dict[str, Any]:
    checks = _run_component_checks(component)
    now = datetime.now(timezone.utc)
    total = len(checks) or 1
    passed = sum(1 for item in checks if item["passed"])
    success_rate = passed / total
    metric = {
        "router": "router_accuracy",
        "tool": "tool_contract_validity",
        "rag": "rag_keyword_relevance",
        "schema": "schema_compliance",
        "llm": "llm_rubric_readiness",
    }.get(component, "component_score")
    results = []
    for index, check in enumerate(checks):
        case_id = check["case_id"]
        score = 1.0 if check["passed"] else 0.0
        trial = {
            "case_id": case_id,
            "trial_number": 0,
            "scores": {metric: score},
            "weighted_score": score,
            "error": None if check["passed"] else check.get("reason"),
            "tool_calls": check.get("tool_calls", []),
            "expected_scene": component,
            "actual_scene": component,
            "expected_worker": check.get("expected"),
            "actual_worker": check.get("actual"),
            "duration_ms": 0.0,
            "error_reason": None if check["passed"] else check.get("reason"),
            "missing_metrics": [],
            "threshold_failures": [] if check["passed"] else [{"metric": metric, "score": score, "threshold": 1.0}],
            "failure_class": "none" if check["passed"] else "eval_framework_error",
            "final_answer_preview": check.get("summary", ""),
            "trace_timeline": [
                {
                    "index": 0,
                    "event_type": f"{component}_component",
                    "timestamp": now.timestamp(),
                    "label": check.get("label") or f"组件评测：{component}",
                    "tool_name": check.get("tool_name"),
                    "duration_ms": 0.0,
                    "data": check,
                }
            ],
        }
        results.append({
            "case_id": case_id,
            "category": "component",
            "scene": component,
            "task": check.get("task") or f"验证 {component} 组件契约 #{index + 1}",
            "priority": "p1",
            "success_rate": score,
            "avg_scores": {metric: score},
            "trials": [trial],
        })
    report = {
        "metadata": {
            "suite": f"component:{component}",
            "runner": "component",
            "dataset": dataset,
            "owner": owner,
            "report_schema_version": "1.2",
        },
        "timestamp": now.isoformat(),
        "total_cases": len(results),
        "total_trials": len(results),
        "overall_success_rate": success_rate,
        "category_breakdown": {"component": {"success_rate": success_rate}},
        "scene_breakdown": {component: {"success_rate": success_rate}},
        "failure_summary": _failure_summary(results),
        "duration_seconds": 0.0,
        "results": results,
    }
    report["stability"] = _stability_from_results(results)
    return report


def _run_component_checks(component: str) -> list[dict[str, Any]]:
    if component == "router":
        return _router_checks()
    if component == "tool":
        return _tool_checks()
    if component == "rag":
        return _rag_checks()
    if component == "schema":
        return _schema_checks()
    if component == "llm":
        return _llm_checks()
    return [{"case_id": "unknown-component", "passed": False, "reason": f"unsupported component: {component}"}]


def _router_checks() -> list[dict[str, Any]]:
    from app.agent.supervisor.graph import route_agent_request

    cases = [
        ("router-eat-out", "静安寺附近吃火锅，人均100", "chat", "food_advisor", "eat_out"),
        ("router-home-chef", "冰箱有鸡蛋番茄面条，做什么", "chat", "home_chef", "cook_home"),
        ("router-route", "从静安寺到外滩怎么走", "chat", "route_planner", "route"),
        ("router-travel", "帮我规划一个3天的成都旅行", "chat", "travel_planner", "travel"),
        ("router-chat", "你好", "chat", "general_chat", "chat"),
    ]
    checks = []
    for case_id, message, scene, expected_worker, expected_intent in cases:
        decision = route_agent_request({"message": message, "scene": scene})
        actual_worker = getattr(decision, "worker", None)
        actual_intent = getattr(decision, "intent", None)
        passed = actual_worker == expected_worker and actual_intent == expected_intent
        checks.append({
            "case_id": case_id,
            "task": message,
            "label": f"路由 {message}",
            "expected": expected_worker,
            "actual": actual_worker,
            "expected_intent": expected_intent,
            "actual_intent": actual_intent,
            "confidence": getattr(decision, "confidence", None),
            "reason": None if passed else f"expected {expected_worker}/{expected_intent}, got {actual_worker}/{actual_intent}",
            "passed": passed,
            "summary": f"{message} -> {actual_worker}/{actual_intent}",
        })
    return checks


def _tool_checks() -> list[dict[str, Any]]:
    from app.agent.tools import describe_tools

    checks = []
    for tool in describe_tools():
        schema = tool.get("input_schema")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        passed = bool(tool.get("name")) and isinstance(tool.get("description"), str) and isinstance(schema, dict) and isinstance(properties, dict)
        checks.append({
            "case_id": f"tool-contract-{tool.get('name')}",
            "task": f"验证工具 {tool.get('name')} schema",
            "label": f"工具契约 {tool.get('name')}",
            "tool_name": tool.get("name"),
            "expected": "valid_tool_schema",
            "actual": "valid_tool_schema" if passed else "invalid_tool_schema",
            "passed": passed,
            "reason": None if passed else "tool must have name, description and object input schema",
            "summary": f"{tool.get('name')} schema fields={len(properties or {})}",
        })
    return checks


def _rag_checks() -> list[dict[str, Any]]:
    from app.agent.rag.base import keyword_score, tokenize

    cases = [
        ("rag-keyword-recipe", "番茄 鸡蛋 面", "番茄鸡蛋面是一道快手家常菜", 0.6),
        ("rag-keyword-restaurant", "静安寺 火锅", "静安寺附近火锅餐厅适合聚餐", 0.8),
    ]
    checks = []
    for case_id, query, text, threshold in cases:
        score = keyword_score(query, text)
        passed = score >= threshold
        checks.append({
            "case_id": case_id,
            "task": f"RAG keyword relevance: {query}",
            "label": "RAG 关键词相关性",
            "expected": f">={threshold}",
            "actual": score,
            "query_tokens": tokenize(query),
            "passed": passed,
            "reason": None if passed else f"keyword_score {score:.2f} < {threshold:.2f}",
            "summary": f"{query} score={score:.2f}",
        })
    return checks


def _schema_checks() -> list[dict[str, Any]]:
    from app.agent.schemas import FinalAnswerArgs

    cases = [
        ("schema-restaurant", {"recommendations": [{"type": "restaurant", "title": "某某火锅", "reason": "距离近"}], "followups": ["要我帮你导航吗？"]}),
        ("schema-recipe", {"recommendations": [{"type": "recipe", "title": "番茄鸡蛋面", "time": 15}], "warnings": []}),
        ("schema-note", {"recommendations": [{"type": "note", "title": "证据不足", "reason": "需要更多偏好"}]}),
    ]
    checks = []
    for case_id, payload in cases:
        try:
            parsed = FinalAnswerArgs.model_validate(payload)
            passed = True
            reason = None
            actual = parsed.model_dump()
        except Exception as exc:
            passed = False
            reason = str(exc)
            actual = {}
        checks.append({
            "case_id": case_id,
            "task": "验证最终回答 schema",
            "label": "FinalAnswerArgs schema",
            "expected": "valid",
            "actual": "valid" if passed else "invalid",
            "payload": payload,
            "parsed": actual,
            "passed": passed,
            "reason": reason,
            "summary": "schema valid" if passed else "schema invalid",
        })
    return checks


def _llm_checks() -> list[dict[str, Any]]:
    try:
        from evals.rubric import get_full_rubric_config

        rubric = get_full_rubric_config()
    except Exception as exc:
        return [{
            "case_id": "llm-rubric-load",
            "task": "加载 LLM Judge rubric",
            "label": "Judge rubric readiness",
            "expected": "rubric_available",
            "actual": "missing",
            "passed": False,
            "reason": str(exc),
            "summary": "rubric unavailable",
        }]
    dimensions = rubric.get("dimensions") if isinstance(rubric, dict) else None
    passed = isinstance(dimensions, dict) and bool(dimensions)
    return [{
        "case_id": "llm-rubric-load",
        "task": "加载 LLM Judge rubric",
        "label": "Judge rubric readiness",
        "expected": "rubric_available",
        "actual": sorted(dimensions.keys()) if isinstance(dimensions, dict) else [],
        "passed": passed,
        "reason": None if passed else "rubric dimensions missing",
        "summary": f"rubric dimensions={len(dimensions or {})}",
    }]


def _failure_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [result for result in results if float(result.get("success_rate") or 0.0) < 1.0]
    if not failed:
        return {}
    return {
        "by_case": {str(result.get("case_id")): 1 for result in failed},
        "by_metric": {"component_score": len(failed)},
        "by_error_reason": {"component_contract_failed": len(failed)},
    }


def _stability_from_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    cases = []
    for result in results:
        score = float(result.get("success_rate") or 0.0)
        cases.append({
            "case_id": result.get("case_id"),
            "trials": 1,
            "pass_count": 1 if score >= 1.0 else 0,
            "pass_at_k": score >= 1.0,
            "pass_all_k": score >= 1.0,
            "scores": [score],
            "variance": 0.0,
            "flaky": False,
        })
    total = len(cases) or 1
    pass_count = sum(1 for item in cases if item["pass_at_k"])
    return {
        "k": 1,
        "pass_at_k": round(pass_count / total, 4),
        "pass_all_k": round(pass_count / total, 4),
        "trial_variance": 0.0,
        "flaky_cases": [],
        "cases": cases,
    }

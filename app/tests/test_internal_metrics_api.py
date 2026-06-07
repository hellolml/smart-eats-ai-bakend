import pytest
import asyncio
from datetime import datetime, timezone

from app.agent.metrics import record_agent_metric, reset_agent_metrics
from app.agent.monitoring import persist_realtime_conversation
from app.agent.realtime_eval import RealtimeEvalResult
from app.common.security import create_access_token
from app.infra.db import AsyncSessionLocal
from app.infra.eval_db import eval_session, init_eval_db
from app.infra.models.chat import ChatSession
from app.infra.models.eval import EvalRunJob
from evals.persistence.postgres import EvalPersistenceStore


def _write_report(path, *, success_rate=1.0, case_id="chat-001", priority="p1", metric=1.0):
    path.write_text(
        f"""
        {{
          "metadata": {{
            "suite": "quick",
            "runner": "fixture",
            "report_schema_version": "1.1"
          }},
          "timestamp": "2026-06-06T00:00:00",
          "total_cases": 1,
          "total_trials": 1,
          "overall_success_rate": {success_rate},
          "category_breakdown": {{"normal": {{"success_rate": {success_rate}}}}},
          "scene_breakdown": {{"chat": {{"success_rate": {success_rate}}}}},
          "failure_summary": {{}},
          "duration_seconds": 0.1,
          "results": [
            {{
              "case_id": "{case_id}",
              "category": "normal",
              "scene": "chat",
              "task": "你好",
              "priority": "{priority}",
              "success_rate": {success_rate},
              "avg_scores": {{"task_success": {metric}}},
              "trials": [
                {{
                  "trial_number": 0,
                  "scores": {{"task_success": {metric}}},
                  "threshold_failures": [],
                  "missing_metrics": [],
                  "failure_class": "none",
                  "trace_timeline": [
                    {{"index": 0, "event_type": "context", "label": "路由到 chat", "data": {{}}}}
                  ]
                }}
              ]
            }}
          ]
        }}
        """,
        encoding="utf-8",
    )


def _report_dict(*, success_rate=1.0, case_id="chat-001", priority="p1", metric=1.0):
    return {
        "metadata": {
            "suite": "quick",
            "runner": "fixture",
            "report_schema_version": "1.1",
        },
        "timestamp": "2026-06-06T00:00:00",
        "total_cases": 1,
        "total_trials": 1,
        "overall_success_rate": success_rate,
        "category_breakdown": {"normal": {"success_rate": success_rate}},
        "scene_breakdown": {"chat": {"success_rate": success_rate}},
        "failure_summary": {},
        "duration_seconds": 0.1,
        "results": [
            {
                "case_id": case_id,
                "category": "normal",
                "scene": "chat",
                "task": "你好",
                "priority": priority,
                "success_rate": success_rate,
                "avg_scores": {"task_success": metric},
                "trials": [
                    {
                        "trial_number": 0,
                        "scores": {"task_success": metric},
                        "threshold_failures": [],
                        "missing_metrics": [],
                        "failure_class": "none",
                        "trace_timeline": [
                            {"index": 0, "event_type": "context", "label": "路由到 chat", "data": {}}
                        ],
                    }
                ],
            }
        ],
    }


def _auth_headers(user_id: str = "test-eval-admin") -> dict[str, str]:
    """Create Authorization headers with a valid access token."""
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth gating tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_endpoints_require_authentication(client):
    """All eval endpoints should return 401 without a bearer token."""
    endpoints = [
        "/api/v1/internal/eval-access",
        "/api/v1/internal/eval-reports",
        "/api/v1/internal/eval-report",
        "/api/v1/internal/eval-report/case?report=latest.json&case_id=x",
        "/api/v1/internal/eval-report/compare?baseline=a.json&candidate=b.json",
        "/api/v1/internal/eval-jobs",
        "/api/v1/internal/metrics/agent",
    ]
    for url in endpoints:
        resp = await client.get(url)
        assert resp.status_code == 401, f"{url} should return 401 without token"


@pytest.mark.asyncio
async def test_eval_access_returns_allowed_for_authenticated_user(client):
    """When no whitelist is configured, any authenticated user is allowed."""
    resp = await client.get("/api/v1/internal/eval-access", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["allowed"] is True
    assert data["user_id"] == "test-eval-admin"


@pytest.mark.asyncio
async def test_eval_access_denied_for_non_whitelisted_user(client, monkeypatch):
    """When a phone whitelist is set, users whose phone is not on it get 403."""
    monkeypatch.setenv("EVAL_ADMIN_PHONES", "13800001111,13900002222")
    # No DB user lookup in test env, so phone will be None → 403
    resp = await client.get("/api/v1/internal/eval-access", headers=_auth_headers("random-user"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_eval_access_allowed_when_no_whitelist(client, monkeypatch):
    """When no EVAL_ADMIN_PHONES is set, any authenticated user is allowed (dev default)."""
    monkeypatch.delenv("EVAL_ADMIN_PHONES", raising=False)
    resp = await client.get("/api/v1/internal/eval-access", headers=_auth_headers("any-user"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["allowed"] is True


# ---------------------------------------------------------------------------
# Original functional tests (now with auth headers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_metrics_agent_endpoint(client):
    reset_agent_metrics()
    record_agent_metric("s-metrics", "fallback_final")
    record_agent_metric("s-metrics", "non_fallback_final")
    record_agent_metric("s-metrics", "non_fallback_final")

    resp = await client.get("/api/v1/internal/metrics/agent", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"]["fallback_final"] == 1
    assert data["metrics"]["non_fallback_final"] == 2
    assert data["summary"]["total_final"] == 3


@pytest.mark.asyncio
async def test_internal_metrics_agent_endpoint_reset(client):
    reset_agent_metrics()
    record_agent_metric("s-metrics-2", "fallback_final")

    resp = await client.get("/api/v1/internal/metrics/agent?reset=true", headers=_auth_headers())
    assert resp.status_code == 200

    resp2 = await client.get("/api/v1/internal/metrics/agent", headers=_auth_headers())
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["metrics"].get("fallback_final", 0) == 0


@pytest.mark.asyncio
async def test_internal_eval_report_endpoint_reads_latest(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path))
    _write_report(tmp_path / "latest.json")

    resp = await client.get("/api/v1/internal/eval-report", headers=_auth_headers())

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["selected"] == "latest.json"
    assert data["report"]["results"][0]["case_id"] == "chat-001"
    assert data["reports"][0]["name"] == "latest.json"


@pytest.mark.asyncio
async def test_internal_eval_reports_endpoint_lists_summaries(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path))
    _write_report(tmp_path / "latest.json", success_rate=0.0, priority="p0", metric=0.0)
    _write_report(tmp_path / "eval_report_20260606_000000.json")

    resp = await client.get("/api/v1/internal/eval-reports", headers=_auth_headers())

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["reports"]) == 2
    latest = next(item for item in data["reports"] if item["name"] == "latest.json")
    assert latest["failed_cases"] == 1
    assert latest["p0_failed_cases"] == 1
    assert latest["suite"] == "quick"
    assert latest["runner"] == "fixture"


@pytest.mark.asyncio
async def test_internal_eval_report_compare_and_case_detail(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path))
    _write_report(tmp_path / "baseline.json", success_rate=1.0, metric=1.0)
    _write_report(tmp_path / "candidate.json", success_rate=0.0, priority="p0", metric=0.2)

    compare = await client.get(
        "/api/v1/internal/eval-report/compare?baseline=baseline.json&candidate=candidate.json",
        headers=_auth_headers(),
    )
    assert compare.status_code == 200
    compare_data = compare.json()
    assert compare_data["summary_delta"]["overall_success_rate"] == -1.0
    assert compare_data["case_changes"]["regressions"][0]["case_id"] == "chat-001"
    assert compare_data["case_changes"]["score_drops"][0]["metric"] == "task_success"

    detail = await client.get(
        "/api/v1/internal/eval-report/case?report=candidate.json&case_id=chat-001",
        headers=_auth_headers(),
    )
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["case"]["case_id"] == "chat-001"
    assert detail_data["trials"][0]["trace_timeline"][0]["event_type"] == "context"


@pytest.mark.asyncio
async def test_internal_eval_report_endpoints_read_database_first(client, tmp_path, monkeypatch):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'evals.db'}"
    monkeypatch.setenv("EVAL_DATABASE_URL", database_url)
    store = EvalPersistenceStore(database_url)
    try:
        await store.upsert_report("baseline.json", _report_dict(success_rate=1.0, metric=1.0))
        await store.upsert_report("candidate.json", _report_dict(success_rate=0.0, priority="p0", metric=0.2))
    finally:
        await store.close()

    reports = await client.get("/api/v1/internal/eval-reports", headers=_auth_headers())
    assert reports.status_code == 200
    reports_data = reports.json()
    assert reports_data["source"] == "db"
    assert {item["name"] for item in reports_data["reports"]} == {"baseline.json", "candidate.json"}

    detail = await client.get(
        "/api/v1/internal/eval-report/case?report=candidate.json&case_id=chat-001",
        headers=_auth_headers(),
    )
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["source"] == "db"
    assert detail_data["trials"][0]["trace_timeline"][0]["event_type"] == "context"

    compare = await client.get(
        "/api/v1/internal/eval-report/compare?baseline=baseline.json&candidate=candidate.json",
        headers=_auth_headers(),
    )
    assert compare.status_code == 200
    compare_data = compare.json()
    assert compare_data["source"] == "db"
    assert compare_data["case_changes"]["regressions"][0]["case_id"] == "chat-001"


@pytest.mark.asyncio
async def test_internal_eval_reports_exclude_realtime_compat_runs(client, tmp_path, monkeypatch):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'evals.db'}"
    monkeypatch.setenv("EVAL_DATABASE_URL", database_url)
    store = EvalPersistenceStore(database_url)
    now = datetime.now(timezone.utc)
    try:
        await store.upsert_report("offline.json", _report_dict(success_rate=1.0, metric=1.0))
        await store.init_schema()
        async with store.session_factory() as session:
            await persist_realtime_conversation(
                session,
                result=RealtimeEvalResult(
                    id="realtime-compat-run",
                    session_id="compat-session",
                    scene="chat",
                    agent_id="chat_worker",
                    has_content=True,
                    overall_quality=0.9,
                    total_duration_ms=100,
                ),
                events=[{"event": "final", "data": {"answer": {"state": "completed"}}}],
                final_json={"state": "completed"},
                user_id="user-1",
                trace_id="trace-1",
                model_provider="test",
                model_name="fixture",
                started_at=now,
                ended_at=now,
            )
            await session.commit()
    finally:
        await store.close()

    reports = await client.get("/api/v1/internal/eval-reports", headers=_auth_headers())
    assert reports.status_code == 200
    names = {item["name"] for item in reports.json()["reports"]}
    assert "offline.json" in names
    assert all(not name.startswith("realtime/") for name in names)

    latest = await client.get("/api/v1/internal/eval-report?report=latest.json", headers=_auth_headers())
    assert latest.status_code == 200
    assert latest.json()["report"]["results"][0]["case_id"] == "chat-001"


@pytest.mark.asyncio
async def test_internal_eval_report_endpoint_rejects_path_traversal(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path))

    resp = await client.get(
        "/api/v1/internal/eval-report?report=../secrets.json",
        headers=_auth_headers(),
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_eval_job_endpoint_rejects_invalid_payload(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'eval_jobs.db'}")
    monkeypatch.setenv("EVAL_WEB_JOB_OUTPUT_DIR", str(tmp_path / "web_jobs"))

    resp = await client.post(
        "/api/v1/internal/eval-jobs",
        headers=_auth_headers(),
        json={"runner": "bad", "suite": "quick"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_eval_job_endpoint_rejects_when_active_job_exists(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'eval_jobs.db'}")
    monkeypatch.setenv("EVAL_WEB_JOB_OUTPUT_DIR", str(tmp_path / "web_jobs"))
    await init_eval_db()
    async with eval_session() as session:
        session.add(EvalRunJob(
            id="active-job",
            status="running",
            runner="fixture",
            suite="quick",
            num_trials=1,
            output_dir=str(tmp_path / "web_jobs" / "active-job"),
        ))
        await session.commit()

    resp = await client.post(
        "/api/v1/internal/eval-jobs",
        headers=_auth_headers(),
        json={"runner": "fixture", "suite": "quick", "num_trials": 1},
    )

    assert resp.status_code == 409
    assert "active-job" in resp.json()["message"]


@pytest.mark.asyncio
async def test_eval_job_fixture_quick_runs_to_completion(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'eval_jobs.db'}")
    monkeypatch.setenv("EVAL_WEB_JOB_OUTPUT_DIR", str(tmp_path / "web_jobs"))

    created = await client.post(
        "/api/v1/internal/eval-jobs",
        headers=_auth_headers(),
        json={"runner": "fixture", "suite": "quick", "num_trials": 1, "persist_db": True},
    )
    assert created.status_code == 200
    job_id = created.json()["job"]["id"]

    detail = None
    for _ in range(80):
        resp = await client.get(f"/api/v1/internal/eval-jobs/{job_id}", headers=_auth_headers())
        assert resp.status_code == 200
        detail = resp.json()["job"]
        if detail["status"] in {"succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.1)

    assert detail is not None
    assert detail["status"] == "succeeded"
    assert detail["report_name"].startswith("eval_report_")
    assert "Eval DB persisted" in detail["logs_tail"]

    reports = await client.get("/api/v1/internal/eval-reports", headers=_auth_headers())
    assert reports.status_code == 200
    names = {item["name"] for item in reports.json()["reports"]}
    assert detail["report_name"] in names


@pytest.mark.asyncio
async def test_monitoring_endpoints_return_empty_state(client):
    for url in [
        "/api/v1/internal/monitoring/overview?window=1h",
        "/api/v1/internal/monitoring/traces",
        "/api/v1/internal/monitoring/failures?window=24h",
        "/api/v1/internal/monitoring/cost-latency?window=24h",
        "/api/v1/internal/monitoring/safety?window=24h",
        "/api/v1/internal/monitoring/reviews?decision=pending",
    ]:
        resp = await client.get(url, headers=_auth_headers())
        assert resp.status_code == 200, url


@pytest.mark.asyncio
async def test_monitoring_api_reads_conversation_tables_and_reviews(client):
    async with AsyncSessionLocal() as session:
        session.add(ChatSession(
            id="monitoring-session",
            user_id="monitoring-user",
            scene="eat_out",
            title="周末寿司聚餐推荐",
        ))
        await session.commit()

    result = RealtimeEvalResult(
        id="monitoring-test-run-1",
        session_id="monitoring-session",
        scene="eat_out",
        agent_id="restaurant_worker",
        has_content=True,
        overall_quality=0.92,
        schema_compliance=1.0,
        no_leak=1.0,
        total_duration_ms=1234,
        tool_call_count=1,
        tool_names=["restaurant_search"],
    )
    events = [
        {"event": "context", "data": {"scene": "eat_out", "agent_id": "restaurant_worker"}},
        {"event": "tool_call", "data": {"name": "restaurant_search", "args": {"q": "sushi"}, "latency_ms": 120}},
        {"event": "tool_result", "data": {"name": "restaurant_search", "output_preview": "ok"}},
        {
            "event": "final",
            "data": {
                "answer": {"state": "completed", "scene": "eat_out"},
                "agent_result": {
                    "status": "completed",
                    "worker": "food_advisor",
                    "final": {"state": "completed", "scene": "eat_out"},
                    "diagnostics": {
                        "model_config": {
                            "source": "env",
                            "provider": "openai",
                            "provider_value": "openai:kimi-k2.5",
                            "model_planner": "kimi-k2.5",
                            "api_key": "must-not-leak",
                        }
                    },
                },
            },
        },
    ]
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        await persist_realtime_conversation(
            session,
            result=result,
            events=events,
            final_json={"state": "completed", "scene": "eat_out"},
            user_id="monitoring-user",
            trace_id="trace-monitoring-test",
            model_provider="test",
            model_name="fixture",
            started_at=now,
            ended_at=now,
        )
        await session.commit()

    overview = await client.get("/api/v1/internal/monitoring/overview?window=24h", headers=_auth_headers())
    assert overview.status_code == 200
    assert overview.json()["total_runs"] >= 1

    traces = await client.get("/api/v1/internal/monitoring/traces?scene=eat_out", headers=_auth_headers())
    assert traces.status_code == 200
    trace_rows = traces.json()["records"]
    assert any(row["id"] == "monitoring-test-run-1" for row in trace_rows)
    target_trace = next(row for row in trace_rows if row["id"] == "monitoring-test-run-1")
    assert target_trace["session_title"] == "周末寿司聚餐推荐"
    assert target_trace["title"] == "周末寿司聚餐推荐"
    assert target_trace["model_config"]["provider_value"] == "openai:kimi-k2.5"
    assert target_trace["model_config"]["model_planner"] == "kimi-k2.5"
    assert "api_key" not in target_trace["model_config"]

    detail = await client.get("/api/v1/internal/monitoring/traces/monitoring-test-run-1", headers=_auth_headers())
    assert detail.status_code == 200
    assert detail.json()["run"]["session_title"] == "周末寿司聚餐推荐"
    assert detail.json()["run"]["model_config"]["provider_value"] == "openai:kimi-k2.5"
    assert detail.json()["events"][1]["tool_name"] == "restaurant_search"

    live_sessions = await client.get("/api/v1/internal/eval-hub/live-sessions?window=24h", headers=_auth_headers())
    assert live_sessions.status_code == 200
    live_rows = live_sessions.json()["records"]
    target_session = next(row for row in live_rows if row["session_id"] == "monitoring-session")
    assert target_session["session_title"] == "周末寿司聚餐推荐"

    review = await client.post(
        "/api/v1/internal/monitoring/reviews/monitoring-test-run-1",
        headers=_auth_headers(),
        json={"decision": "accepted", "notes": "ok"},
    )
    assert review.status_code == 200
    assert review.json()["review"]["decision"] == "accepted"


@pytest.mark.asyncio
async def test_monitoring_persists_model_usage_cost_and_alerts(client):
    result = RealtimeEvalResult(
        id="monitoring-cost-run-1",
        session_id="monitoring-cost-session",
        scene="chat",
        agent_id="chat_worker",
        has_content=False,
        overall_quality=0.1,
        schema_compliance=1.0,
        no_leak=1.0,
        total_duration_ms=20000,
        error="provider rate limit",
        error_reason="429 rate limit",
    )
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        await persist_realtime_conversation(
            session,
            result=result,
            events=[
                {"event": "model_usage", "data": {"provider": "openai", "model": "gpt-5.5", "usage": {"input_tokens": 1000, "output_tokens": 2000}}},
                {"event": "model_usage", "data": {"provider": "openai", "model": "gpt-5.5", "usage": {"input_tokens": 300, "output_tokens": 400}}},
                {"event": "error", "data": {"code": "429", "message": "provider rate limit"}},
            ],
            final_json=None,
            user_id="monitoring-user",
            trace_id="trace-cost-test",
            model_provider="openai",
            model_name="gpt-5.5",
            started_at=now,
            ended_at=now,
        )
        from app.agent.monitoring import evaluate_alert_rules

        alerts = await evaluate_alert_rules(session, since=now.replace(hour=0, minute=0, second=0, microsecond=0), notify=False)
        await session.commit()

    cost = await client.get("/api/v1/internal/monitoring/cost-latency?window=24h", headers=_auth_headers())
    assert cost.status_code == 200
    payload = cost.json()
    assert payload["token_input"] >= 1300
    assert payload["token_output"] >= 2400
    assert payload["latency_p99_ms"] >= 0

    failures = await client.get("/api/v1/internal/monitoring/failures?window=24h", headers=_auth_headers())
    assert failures.status_code == 200
    assert failures.json()["by_failure_class"]["provider_rate_limit"] >= 1

    assert any(item["alert_type"] == "provider_error_rate" for item in alerts)
    alert_resp = await client.get("/api/v1/internal/eval-alerts?status=open", headers=_auth_headers())
    assert alert_resp.status_code == 200
    assert any(item["alert_type"] == "provider_error_rate" for item in alert_resp.json()["alerts"])


@pytest.mark.asyncio
async def test_eval_run_outcome_and_judge_api_can_read_eval_database_report(client, tmp_path, monkeypatch):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'evals.db'}"
    monkeypatch.setenv("EVAL_DATABASE_URL", database_url)
    report = _report_dict(success_rate=1.0)
    trial = report["results"][0]["trials"][0]
    trial["outcome_details"] = [
        {
            "verifier": "schema_state_verifier",
            "score": 1.0,
            "passed": True,
            "failures": [],
            "details": {"checked_fields": ["state"]},
        }
    ]
    trial["judge_scores"] = {"answer_relevance": 0.9}
    trial["judge_reasons"] = {"answer_relevance": "relevant"}
    store = EvalPersistenceStore(database_url)
    try:
        await store.upsert_report("with-outcomes.json", report)
    finally:
        await store.close()

    outcomes = await client.get("/api/v1/internal/eval-runs/with-outcomes.json/outcomes", headers=_auth_headers())
    assert outcomes.status_code == 200
    assert outcomes.json()["outcomes"][0]["verifier"] == "schema_state_verifier"

    judge = await client.get("/api/v1/internal/eval-runs/with-outcomes.json/judge-results", headers=_auth_headers())
    assert judge.status_code == 200
    assert judge.json()["judge_results"][0]["metric"] == "answer_relevance"


@pytest.mark.asyncio
async def test_review_can_convert_trace_to_dataset_case(client):
    result = RealtimeEvalResult(
        id="monitoring-dataset-run-1",
        session_id="monitoring-dataset-session",
        scene="chat",
        agent_id="chat_worker",
        has_content=True,
        overall_quality=0.5,
        total_duration_ms=100,
    )
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        await persist_realtime_conversation(
            session,
            result=result,
            events=[{"event": "final", "data": {"answer": {"state": "completed"}}}],
            final_json={"state": "completed"},
            user_id="monitoring-user",
            trace_id="trace-dataset-test",
            model_provider="test",
            model_name="fixture",
            started_at=now,
            ended_at=now,
        )
        await session.commit()

    review = await client.post(
        "/api/v1/internal/monitoring/reviews/monitoring-dataset-run-1",
        headers=_auth_headers(),
        json={
            "decision": "converted_to_case",
            "dataset": "regression",
            "dataset_version": "draft",
            "expected_behavior": "应该给出可用回答",
            "failure_tags": ["low_quality"],
        },
    )
    assert review.status_code == 200
    assert review.json()["converted_case"]["case_id"] == "prod-monitoring-dataset-run-1"

    cases = await client.get("/api/v1/internal/eval-datasets/regression/cases", headers=_auth_headers())
    assert cases.status_code == 200
    assert any(item["case_id"] == "prod-monitoring-dataset-run-1" for item in cases.json()["cases"])


@pytest.mark.asyncio
async def test_eval_datasets_api_lists_readonly_cases(client):
    summary = await client.get("/api/v1/internal/eval-datasets", headers=_auth_headers())
    assert summary.status_code == 200
    suites = {item["suite"] for item in summary.json()["datasets"]}
    assert {"quick", "full", "live-smoke"} <= suites

    cases = await client.get("/api/v1/internal/eval-datasets/quick/cases", headers=_auth_headers())
    assert cases.status_code == 200
    payload = cases.json()
    assert payload["suite"] == "quick"
    assert payload["cases"]
    assert "expectations_summary" in payload["cases"][0]


@pytest.mark.asyncio
async def test_eval_hub_api_covers_trace_dataset_experiment_and_registry(client):
    result = RealtimeEvalResult(
        id="hub-run-1",
        session_id="hub-session-1",
        scene="home_chef",
        agent_id="home_chef",
        has_content=True,
        overall_quality=0.92,
        schema_compliance=1.0,
        no_leak=1.0,
        total_duration_ms=321,
    )
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        await persist_realtime_conversation(
            session,
            result=result,
            events=[
                {"event": "model_usage", "data": {"provider": "openai", "model": "gpt-5.5", "usage": {"input_tokens": 10, "output_tokens": 20}}},
                {"event": "tool_call", "data": {"name": "search_recipes", "args": {"q": "dinner"}, "latency_ms": 30}},
                {"event": "tool_result", "data": {"name": "search_recipes", "output_preview": "ok", "has_error": False}},
                {"event": "final", "data": {"answer": {"state": "completed"}}},
            ],
            final_json={"state": "completed"},
            user_id="hub-user",
            trace_id="hub-trace-1",
            model_provider="openai",
            model_name="gpt-5.5",
            started_at=now,
            ended_at=now,
        )
        await session.commit()

    overview = await client.get("/api/v1/internal/eval-hub/overview?window=24h", headers=_auth_headers())
    assert overview.status_code == 200
    assert overview.json()["monitoring"]["total_runs"] >= 1

    traces = await client.get("/api/v1/internal/eval-hub/traces?window=24h", headers=_auth_headers())
    assert traces.status_code == 200
    assert any(item["trace_id"] == "hub-trace-1" for item in traces.json()["records"])

    detail = await client.get("/api/v1/internal/eval-hub/traces/hub-trace-1", headers=_auth_headers())
    assert detail.status_code == 200
    assert any(span["span_type"] == "llm_call" for span in detail.json()["spans"])
    assert any(span["span_type"] == "tool_call" for span in detail.json()["spans"])

    case_resp = await client.post(
        "/api/v1/internal/eval-hub/traces/hub-trace-1/dataset-cases",
        headers=_auth_headers(),
        json={"dataset": "regression", "version": "draft"},
    )
    assert case_resp.status_code == 200
    assert case_resp.json()["case"]["source"] == "production_trace"

    evaluator = await client.post(
        "/api/v1/internal/eval-hub/evaluators",
        headers=_auth_headers(),
        json={"name": "custom_quality", "type": "rule", "version": "v1", "threshold": 0.7},
    )
    assert evaluator.status_code == 200
    evaluators = await client.get("/api/v1/internal/eval-hub/evaluators", headers=_auth_headers())
    assert any(item["name"] == "custom_quality" for item in evaluators.json()["evaluators"])

    experiment = await client.post(
        "/api/v1/internal/eval-hub/experiments",
        headers=_auth_headers(),
        json={"name": "hub experiment", "dataset_name": "regression", "dataset_version": "draft"},
    )
    assert experiment.status_code == 200
    experiment_id = experiment.json()["experiment"]["id"]
    run_resp = await client.post(
        f"/api/v1/internal/eval-hub/experiments/{experiment_id}/runs",
        headers=_auth_headers(),
        json={"report_name": "latest.json", "role": "candidate"},
    )
    assert run_resp.status_code == 200

    playground = await client.post(
        "/api/v1/internal/eval-hub/playground/runs",
        headers=_auth_headers(),
        json={"input": "hello", "outputs": [], "scores": {}},
    )
    assert playground.status_code == 200

    scenario = await client.post(
        "/api/v1/internal/eval-hub/simulations/scenarios",
        headers=_auth_headers(),
        json={"name": "dinner simulation", "scenario": {"max_turns": 3, "success_criteria": ["complete"]}},
    )
    assert scenario.status_code == 200
    sim_run = await client.post(
        f"/api/v1/internal/eval-hub/simulations/scenarios/{scenario.json()['scenario']['id']}/runs",
        headers=_auth_headers(),
    )
    assert sim_run.status_code == 200


@pytest.mark.asyncio
async def test_eval_console_static_page_served(client):
    resp = await client.get("/evals.html")

    assert resp.status_code == 200
    assert "Smart Eats 评测控制台" in resp.text
    assert "/api/v1/internal/eval-report" in resp.text
    assert "/api/v1/internal/eval-reports" in resp.text
    assert "运行历史" in resp.text
    assert "运行对比" in resp.text
    assert "用例详情" in resp.text
    assert "失败分析" in resp.text

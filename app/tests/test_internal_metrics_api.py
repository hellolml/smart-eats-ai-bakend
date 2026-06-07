import pytest

from app.agent.metrics import record_agent_metric, reset_agent_metrics
from app.common.security import create_access_token
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
async def test_internal_eval_report_endpoint_rejects_path_traversal(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path))

    resp = await client.get(
        "/api/v1/internal/eval-report?report=../secrets.json",
        headers=_auth_headers(),
    )

    assert resp.status_code == 400


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

import pytest

from app.agent.metrics import record_agent_metric, reset_agent_metrics


@pytest.mark.asyncio
async def test_internal_metrics_agent_endpoint(client):
    reset_agent_metrics()
    record_agent_metric("s-metrics", "fallback_final")
    record_agent_metric("s-metrics", "non_fallback_final")
    record_agent_metric("s-metrics", "non_fallback_final")

    resp = await client.get("/api/v1/internal/metrics/agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"]["fallback_final"] == 1
    assert data["metrics"]["non_fallback_final"] == 2
    assert data["summary"]["total_final"] == 3


@pytest.mark.asyncio
async def test_internal_metrics_agent_endpoint_reset(client):
    reset_agent_metrics()
    record_agent_metric("s-metrics-2", "fallback_final")

    resp = await client.get("/api/v1/internal/metrics/agent?reset=true")
    assert resp.status_code == 200

    resp2 = await client.get("/api/v1/internal/metrics/agent")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["metrics"].get("fallback_final", 0) == 0

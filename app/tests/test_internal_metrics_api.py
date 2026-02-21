import pytest

from app.agent.graph import _record_metric, reset_agent_metrics
from app.agent.state import ChatState


@pytest.mark.asyncio
async def test_internal_metrics_agent_endpoint(client):
    reset_agent_metrics()
    state = ChatState(session_id="s-metrics")
    _record_metric(state, "fallback_final")
    _record_metric(state, "non_fallback_final")
    _record_metric(state, "non_fallback_final")

    resp = await client.get("/api/v1/internal/metrics/agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"]["fallback_final"] == 1
    assert data["metrics"]["non_fallback_final"] == 2
    assert data["summary"]["total_final"] == 3


@pytest.mark.asyncio
async def test_internal_metrics_agent_endpoint_reset(client):
    reset_agent_metrics()
    state = ChatState(session_id="s-metrics-2")
    _record_metric(state, "fallback_final")

    resp = await client.get("/api/v1/internal/metrics/agent?reset=true")
    assert resp.status_code == 200

    resp2 = await client.get("/api/v1/internal/metrics/agent")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["metrics"].get("fallback_final", 0) == 0

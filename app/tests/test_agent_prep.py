from __future__ import annotations

import pytest

from app.domain.app import agent_prep


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeMessage:
    def __init__(self, answer):
        self.tool_payload_json = {"answer": answer}


class _FakeDb:
    async def execute(self, _statement):
        return _FakeResult(
            [
                _FakeMessage(
                    {
                        "state": "candidates_ready",
                        "plan_type": "travel",
                        "candidates": [{"name": "西湖"}],
                    }
                )
            ]
        )


@pytest.mark.asyncio
async def test_prepare_supervisor_payload_uses_structured_agent_context():
    payload = await agent_prep.prepare_supervisor_payload(
        _FakeDb(),
        "s1",
        None,
        {
            "message": "继续旅行计划",
            "client_context_overrides": {"environment": {"location": {"lat": 30.2}}},
        },
    )

    overrides = payload["client_context_overrides"]
    assert overrides["environment"]["location"]["lat"] == 30.2
    assert overrides["latest_travel_final_json"]["state"] == "candidates_ready"

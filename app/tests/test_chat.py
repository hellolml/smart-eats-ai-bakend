import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_chat_stream_stop(client):
    resp = await client.post("/api/v1/chat/sessions")
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    got_tool_call = False
    got_delta = False
    got_final = False
    stopped_flag = None
    current_event = None
    async def send_stop():
        await asyncio.sleep(0.1)
        stop_resp = await client.post(f"/api/v1/chat/sessions/{session_id}/stop")
        assert stop_resp.status_code == 200

    stop_task = asyncio.create_task(send_stop())

    async with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{session_id}/stream",
        json={"message": "quick dinner"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}

                if current_event == "tool_call":
                    got_tool_call = True
                if current_event == "delta":
                    got_delta = True
                if current_event == "final":
                    got_final = True
                    stopped_flag = payload.get("stopped")
                    break

    await stop_task

    assert got_final
    assert stopped_flag is True

import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_app_chat_stream_stop(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_chat@example.com", "password": "secret123", "name": "chatter"},
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    resp = await client.post("/api/v1/app/chat/session", headers=headers)
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    resp = await client.get("/api/v1/app/chat/sessions", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["sessions"]

    got_final = False
    stopped_flag = None
    current_event = None

    async def send_stop():
        await asyncio.sleep(0.1)
        stop_resp = await client.post(f"/api/v1/app/chat/session/{session_id}/stop", headers=headers)
        assert stop_resp.status_code == 200

    stop_task = asyncio.create_task(send_stop())

    async with client.stream(
        "POST",
        f"/api/v1/app/chat/session/{session_id}/stream",
        headers=headers,
        json={"message": "quick dinner"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                payload = json.loads(raw)
                if current_event == "final":
                    got_final = True
                    stopped_flag = payload.get("stopped")
                    break

    await stop_task

    assert got_final
    assert stopped_flag is True

    resp = await client.get(f"/api/v1/app/chat/session/{session_id}/messages", headers=headers)
    assert resp.status_code == 200
    assert "messages" in resp.json()["data"]

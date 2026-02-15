import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_app_fridge_crud_and_scan(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_fridge@example.com", "password": "secret123", "name": "cook"},
    )
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/api/v1/app/fridge/ingredients",
        json={"name": "鸡蛋", "quantity": 3, "unit": "个"},
        headers=headers,
    )
    assert resp.status_code == 200
    item = resp.json()["data"]
    assert item["quantity_text"] == "3个"

    resp = await client.patch(
        f"/api/v1/app/fridge/ingredients/{item['id']}",
        json={"quantity": 4},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["quantity"] == 4

    resp = await client.get("/api/v1/app/fridge/ingredients", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1

    resp = await client.post(
        "/api/v1/app/fridge/scan",
        headers=headers,
        files={"file": ("fridge.jpg", b"fake-image", "image/jpeg")},
    )
    assert resp.status_code == 200
    scan_id = resp.json()["data"]["scan_id"]

    status_payload = None
    for _ in range(30):
        resp = await client.get(f"/api/v1/app/fridge/scan/{scan_id}", headers=headers)
        assert resp.status_code == 200
        status_payload = resp.json()["data"]
        if status_payload["status"] in {"success", "failed"}:
            break
        await asyncio.sleep(0.1)

    assert status_payload is not None
    assert status_payload["status"] == "success"

    got_final = False
    current_event = None
    async with client.stream("GET", f"/api/v1/app/fridge/scan/{scan_id}/events", headers=headers) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
                if current_event == "final":
                    assert payload.get("items") is not None
                    got_final = True
                    break

    assert got_final

    resp = await client.post(
        f"/api/v1/app/fridge/scan/{scan_id}/apply",
        headers=headers,
        json={"merge_by_name": True},
    )
    assert resp.status_code == 200
    applied = resp.json()["data"]
    assert applied["applied_count"] >= 1

    resp = await client.delete(f"/api/v1/app/fridge/ingredients/{item['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True

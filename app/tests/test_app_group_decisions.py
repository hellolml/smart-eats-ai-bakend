import pytest


@pytest.mark.asyncio
async def test_app_group_decision_vote_idempotent_and_close(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "group_owner@example.com", "password": "secret123", "name": "Owner"},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/app/group-decisions",
        headers=headers,
        json={
            "title": "周末吃什么",
            "city": "西安",
            "options": [
                {"title": "火锅", "item_type": "restaurant", "meta": {"price": "$$"}},
                {"title": "烧烤", "item_type": "restaurant", "meta": {"price": "$"}},
            ],
        },
    )
    assert create_resp.status_code == 200
    data = create_resp.json()["data"]
    session_id = data["id"]
    first_item_id = data["items"][0]["id"]
    token = data["share_token"]

    vote_resp = await client.post(
        f"/api/v1/app/group-decisions/{session_id}/vote?token={token}",
        json={
            "item_id": first_item_id,
            "voter_name": "Alice",
            "voter_key": "alice-device-1",
        },
    )
    assert vote_resp.status_code == 200
    assert vote_resp.json()["data"]["changed"] is True

    vote_again_resp = await client.post(
        f"/api/v1/app/group-decisions/{session_id}/vote?token={token}",
        json={
            "item_id": first_item_id,
            "voter_name": "Alice",
            "voter_key": "alice-device-1",
        },
    )
    assert vote_again_resp.status_code == 200
    assert vote_again_resp.json()["data"]["changed"] is False

    close_resp = await client.post(f"/api/v1/app/group-decisions/{session_id}/close", headers=headers)
    assert close_resp.status_code == 200
    close_data = close_resp.json()["data"]
    assert close_data["status"] == "closed"
    assert close_data["winner"]["id"] == first_item_id
    assert close_data["total_votes"] == 1

    post_close_vote = await client.post(
        f"/api/v1/app/group-decisions/{session_id}/vote?token={token}",
        json={
            "item_id": first_item_id,
            "voter_name": "Bob",
            "voter_key": "bob-device-1",
        },
    )
    assert post_close_vote.status_code == 400
    assert post_close_vote.json()["code"] == 44002


@pytest.mark.asyncio
async def test_only_creator_can_close_group_decision(client):
    owner_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "group_owner2@example.com", "password": "secret123", "name": "Owner2"},
    )
    assert owner_resp.status_code == 200
    owner_token = owner_resp.json()["data"]["access_token"]
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    other_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "group_other@example.com", "password": "secret123", "name": "Other"},
    )
    assert other_resp.status_code == 200
    other_token = other_resp.json()["data"]["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    create_resp = await client.post(
        "/api/v1/app/group-decisions",
        headers=owner_headers,
        json={
            "title": "谁来决定",
            "options": [
                {"title": "盖浇饭", "item_type": "restaurant", "meta": {}},
                {"title": "面条", "item_type": "restaurant", "meta": {}},
            ],
        },
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["data"]["id"]

    forbidden_close = await client.post(f"/api/v1/app/group-decisions/{session_id}/close", headers=other_headers)
    assert forbidden_close.status_code == 403
    assert forbidden_close.json()["code"] == 44004


@pytest.mark.asyncio
async def test_group_decision_requires_valid_share_token(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "group_owner3@example.com", "password": "secret123", "name": "Owner3"},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/app/group-decisions",
        headers=headers,
        json={
            "title": "午饭投票",
            "options": [
                {"title": "米线", "item_type": "restaurant", "meta": {}},
                {"title": "饺子", "item_type": "restaurant", "meta": {}},
            ],
        },
    )
    assert create_resp.status_code == 200
    data = create_resp.json()["data"]
    session_id = data["id"]
    item_id = data["items"][0]["id"]
    share_token = data["share_token"]

    no_token_result = await client.get(f"/api/v1/app/group-decisions/{session_id}/result")
    assert no_token_result.status_code == 403
    assert no_token_result.json()["code"] == 44005

    bad_token_result = await client.get(f"/api/v1/app/group-decisions/{session_id}/result?token=badtoken")
    assert bad_token_result.status_code == 403
    assert bad_token_result.json()["code"] == 44005

    ok_result = await client.get(f"/api/v1/app/group-decisions/{session_id}/result?token={share_token}")
    assert ok_result.status_code == 200

    no_token_vote = await client.post(
        f"/api/v1/app/group-decisions/{session_id}/vote",
        json={
            "item_id": item_id,
            "voter_name": "Guest",
            "voter_key": "guest-1",
        },
    )
    assert no_token_vote.status_code == 403

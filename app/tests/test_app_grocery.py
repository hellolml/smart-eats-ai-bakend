import pytest


@pytest.mark.asyncio
async def test_app_grocery_list_from_recipe_flow(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_grocery@example.com", "password": "secret123", "name": "grocery"},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # user already has egg in fridge
    add = await client.post(
        "/api/v1/app/fridge/ingredients",
        headers=headers,
        json={"name": "egg", "quantity": 2, "unit": "pcs"},
    )
    assert add.status_code == 200

    create = await client.post(
        "/api/v1/app/grocery-lists/from-recipe",
        headers=headers,
        json={
            "recipe_name": "番茄炒蛋",
            "required_items": [
                {"name": "egg", "quantity": 2, "unit": "pcs", "category": "主料"},
                {"name": "tomato", "quantity": 3, "unit": "pcs", "category": "主料"},
                {"name": "scallion", "quantity": 1, "unit": "pcs", "category": "辅料"},
            ],
        },
    )
    assert create.status_code == 200
    data = create.json()["data"]
    list_id = data["id"]
    assert data["title"] == "番茄炒蛋 食材准备清单"

    item_names = [i["name"] for i in data["items"]]
    assert "egg" not in item_names
    assert "tomato" in item_names
    assert "scallion" in item_names

    tomato = next(i for i in data["items"] if i["name"] == "tomato")
    assert tomato["unit"] == "pcs"

    get_list = await client.get(f"/api/v1/app/grocery-lists/{list_id}", headers=headers)
    assert get_list.status_code == 200
    items = get_list.json()["data"]["items"]
    assert len(items) >= 2

    first_item_id = items[0]["id"]
    toggle = await client.patch(
        f"/api/v1/app/grocery-lists/{list_id}/items/{first_item_id}",
        headers=headers,
        json={"checked": True},
    )
    assert toggle.status_code == 200
    assert toggle.json()["data"]["checked"] is True

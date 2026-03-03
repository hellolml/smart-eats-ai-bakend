import pytest

from app.domain.app.service import AppBffService


@pytest.mark.asyncio
async def test_home_chef_generate_recipes_with_llm_steps(client, monkeypatch):
    async def fake_llm(ingredient_names, count):
        return [
            {
                "title": "番茄鸡蛋面",
                "desc": "酸香开胃",
                "time": "12min",
                "cal": "420kcal",
                "img": "cooking_dish",
                "tag": "快手",
                "ingredients": ["鸡蛋 2个", "番茄 1个", "挂面 100g"],
                "steps": ["烧水", "煮面", "炒番茄鸡蛋", "合并出锅"],
            }
        ][:count]

    monkeypatch.setattr(AppBffService, "_generate_home_chef_recipes_with_llm", fake_llm)

    reg = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "homechef@example.com", "password": "secret123", "name": "chef"},
    )
    assert reg.status_code == 200
    token = reg.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/app/home-chef/recipes/generate",
        headers=headers,
        json={"ingredients": ["鸡蛋", "番茄", "面条"], "count": 1},
    )
    assert resp.status_code == 200
    recipes = resp.json()["data"]["recipes"]
    assert len(recipes) == 1
    assert recipes[0]["title"] == "番茄鸡蛋面"
    assert len(recipes[0]["steps"]) >= 4

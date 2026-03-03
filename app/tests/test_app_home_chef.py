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
                "steps": ["番茄去皮切块", "鸡蛋加2g盐打散", "中火热锅加10ml油", "下蛋液炒至七分熟盛出", "中火炒番茄并加5g糖", "回锅鸡蛋翻炒30秒出锅"],
                "method_markdown": "## 1. 菜名与风味简介\n酸香开胃，适合晚餐。\n\n## 2. 食材清单\n- 鸡蛋 2个\n- 番茄 1个\n\n## 3. 详细烹饪步骤\n1. 番茄去皮切块\n2. 鸡蛋加盐打散\n3. 热锅下油\n4. 炒蛋盛出\n5. 炒番茄出汁\n6. 回锅合炒\n\n## 4. 主厨的“独门绝技”\n- 先炒蛋后回锅更嫩\n\n## 5. 常见翻车避雷指南\n- 番茄不出汁可加5ml清水",
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
    assert "## 3. 详细烹饪步骤" in recipes[0]["method_markdown"]

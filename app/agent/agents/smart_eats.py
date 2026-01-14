from __future__ import annotations

from app.agent.agents.base import default_writer_prompt
from app.common.config import settings
from app.agent.schemas import FinalAnswer
from app.agent.state import ChatState
from app.agent.agent_registry import AgentConfig, create_agent_config, register_agent


def smart_system_prompt(payload: dict) -> str:
    return (
        "你是 SmartEats 的统一规划器，需要根据用户意图判断是“家里做”还是“出去吃”。"
        "当用户明确或暗示在家做饭时，优先调用 get_fridge_items 获取冰箱食材，"
        "并结合食材再调用 search_recipes 给出在家做的推荐；"
        "当用户想外出用餐时，优先调用 search_restaurants；"
        "如需环境信息可调用 get_weather。"
        "返回严格 JSON（tool 或 final），格式必须是:\n"
        "{\n"
        "  \"type\": \"tool\",\n"
        "  \"name\": \"<tool_name>\",\n"
        "  \"args\": {\"key\": \"value\"}\n"
        "}\n"
        "或\n"
        "{\n"
        "  \"type\": \"final\",\n"
        "  \"answer\": {\n"
        "    \"recommendations\": [],\n"
        "    \"followups\": [],\n"
        "    \"warnings\": []\n"
        "  }\n"
        "}\n"
        f"始终使用 {settings.DEFAULT_LANGUAGE} 输出。"
        f"上下文: {payload}"
    )


def _tool_result_handler(state: ChatState, tool_name: str, result: object) -> dict | None:
    if tool_name == "get_fridge_items" and isinstance(result, dict):
        items = result.get("items") if isinstance(result.get("items"), list) else []
        recipes = result.get("recipes") if isinstance(result.get("recipes"), list) else []
        if state.context is None:
            state.context = {}
        state.context["fridge_items"] = items
        if not recipes:
            return None
        fridge_names = [
            str(item.get("name") or "")
            for item in items
            if isinstance(item, dict) and item.get("name")
        ]
        fridge_names_lower = [name.lower() for name in fridge_names]

        def _score(item: dict) -> int:
            title = (item.get("title") or "").lower()
            return sum(1 for name in fridge_names_lower if name in title)

        ranked = sorted(recipes, key=_score, reverse=True) if fridge_names else recipes
        cards = []
        for item in ranked[:3]:
            title_lower = (item.get("title") or "").lower()
            matched = [
                name
                for name, name_lower in zip(fridge_names, fridge_names_lower)
                if name_lower in title_lower
            ]
            reason = f"匹配食材：{', '.join(matched[:3])}" if matched else "根据冰箱食材推荐"
            cards.append(
                {
                    "type": "recipe",
                    "title": item.get("title"),
                    "reason": reason,
                    "calories": item.get("calories"),
                    "time": item.get("time") or item.get("cook_time_min"),
                    "tags": item.get("tags") or [],
                    "image_url": item.get("image_url"),
                }
            )
        return FinalAnswer(
            recommendations=cards,
            followups=["要不要更快手的？", "能接受辣吗？"],
            warnings=[],
        ).model_dump()
    if tool_name == "search_restaurants" and isinstance(result, list):
        cards = []
        for item in result[:3]:
            cards.append(
                {
                    "type": "restaurant",
                    "title": item.get("name") or item.get("title"),
                    "reason": "附近餐厅推荐",
                    "rating": item.get("rating"),
                    "price": item.get("price"),
                    "tags": item.get("tags") or [],
                    "geo": item.get("geo"),
                }
            )
        return FinalAnswer(
            recommendations=cards,
            followups=["想换一种口味吗？", "要不要更便宜一点？"],
            warnings=[],
        ).model_dump()
    if tool_name == "search_recipes" and isinstance(result, list):
        fridge_items = []
        if state.context:
            fridge_items = state.context.get("fridge_items") or []
        fridge_names = [
            str(item.get("name") or "")
            for item in fridge_items
            if isinstance(item, dict) and item.get("name")
        ]
        fridge_names_lower = [name.lower() for name in fridge_names]

        def _score(item: dict) -> int:
            title = (item.get("title") or "").lower()
            return sum(1 for name in fridge_names_lower if name in title)

        ranked = sorted(result, key=_score, reverse=True) if fridge_names else result
        cards = []
        for item in ranked[:3]:
            title_lower = (item.get("title") or "").lower()
            matched = [
                name
                for name, name_lower in zip(fridge_names, fridge_names_lower)
                if name_lower in title_lower
            ]
            reason = "根据冰箱食材推荐" if matched else "适合在家做"
            if matched:
                reason = f"匹配食材：{', '.join(matched[:3])}"
            cards.append(
                {
                    "type": "recipe",
                    "title": item.get("title"),
                    "reason": reason,
                    "calories": item.get("calories"),
                    "time": item.get("time") or item.get("cook_time_min"),
                    "tags": item.get("tags") or [],
                    "image_url": item.get("image_url"),
                }
            )
        return FinalAnswer(
            recommendations=cards,
            followups=["要不要更快手的？", "能接受辣吗？"],
            warnings=[],
        ).model_dump()
    return None


@register_agent
def _smart_eats_agent() -> AgentConfig:
    return create_agent_config(
        name="smart_eats",
        scene="chat",
        tool_names=["get_weather", "get_fridge_items", "search_recipes", "search_restaurants"],
        max_steps=4,
        system_prompt_builder=smart_system_prompt,
        writer_prompt_builder=default_writer_prompt,
        tool_result_handler=_tool_result_handler,
    )

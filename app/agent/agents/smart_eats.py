from __future__ import annotations

from app.agent.agents.base import default_writer_prompt
from app.common.config import settings
from app.agent.schemas import FinalAnswer
from app.agent.state import ChatState
from app.agent.agent_registry import AgentConfig, create_agent_config, register_agent


def smart_system_prompt(payload: dict) -> str:
    return (
        "你是 SmartEats 的统一规划器，需要根据用户意图判断是“家里做”还是“出去吃”。"
        "当用户明确询问路线/导航/怎么走/路线规划时，必须优先调用 plan_route 获取路线，"
        "不得改用 search_restaurants 或 get_weather；"
        "如果路线所需的起点或终点缺失，应返回 final 追问缺失信息。"
        "当用户明确或暗示在家做饭时，优先调用 get_fridge_items 获取冰箱食材，"
        "并结合食材再调用 search_recipes 给出在家做的推荐；"
        "当用户想外出用餐时，若用户提供了城市/地标/门店名称，应先调用 geocode_location 获取坐标，"
        "再调用 search_restaurants；若用户仅说“出去吃”且无位置，先调用 get_ip_location，"
        "若仍无位置再追问城市/地标。"
        "仅当用户明确询问天气/出行天气，或确实需要天气辅助决策时才调用 get_weather，且同一会话只调用一次；"
        "严禁重复调用同一工具与相同参数。"
        "如果已经拿到天气信息，下一步应继续完成主要任务（如餐厅推荐），不要再次调用天气。"
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
    if tool_name == "search_restaurants":
        if isinstance(result, dict) and result.get("error") == "missing_location":
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "还需要你的定位信息，才能推荐附近餐厅。",
                        "reason": "当前没有位置坐标或具体地标。",
                    }
                ],
                followups=["告诉我你所在的城市/地标？", "是否允许使用定位？"],
                warnings=[],
            ).model_dump()
        if isinstance(result, list) and not result:
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "附近没有找到合适的餐厅。",
                        "reason": "可以换个口味或更具体的关键字。",
                    }
                ],
                followups=["想吃什么菜系？", "要不要换个更大的范围？"],
                warnings=[],
            ).model_dump()
        if isinstance(result, list):
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
    if tool_name in {"get_ip_location", "geocode_location"} and isinstance(result, dict):
        if result.get("error"):
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "需要你的具体位置，才能推荐附近餐厅。",
                        "reason": "定位信息不足。",
                    }
                ],
                followups=["告诉我你所在的城市/地标？"],
                warnings=[],
            ).model_dump()
        if state.context is None:
            state.context = {}
        location = {"lat": result.get("lat"), "lng": result.get("lng")}
        state.context["location"] = location
        if result.get("city"):
            state.context["city"] = result.get("city")
        return None
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
    if tool_name == "plan_route" and isinstance(result, dict):
        error = result.get("error")
        if error == "missing_origin":
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "还需要你的出发位置，才能规划路线。",
                        "reason": "起点信息缺失。",
                    }
                ],
                followups=["告诉我你的出发地/地标？", "你现在在哪个城市或位置？"],
                warnings=[],
            ).model_dump()
        if error == "missing_destination":
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "还需要你的目的地，才能规划路线。",
                        "reason": "终点信息缺失。",
                    }
                ],
                followups=["想去哪儿？给我目的地名称。"],
                warnings=[],
            ).model_dump()
        if error:
            return FinalAnswer(
                recommendations=[
                    {"type": "note", "title": "路线规划失败", "reason": "暂时无法获取路线信息。"}
                ],
                followups=["换个出发地或目的地试试？"],
                warnings=[],
            ).model_dump()

        distance = result.get("distance_m")
        duration = result.get("duration_s")
        distance_km = None
        duration_min = None
        try:
            if distance is not None:
                distance_km = float(distance) / 1000
        except (TypeError, ValueError):
            distance_km = None
        try:
            if duration is not None:
                duration_min = float(duration) / 60
        except (TypeError, ValueError):
            duration_min = None
        summary = "路线规划完成"
        if distance_km is not None and duration_min is not None:
            summary = f"预计{distance_km:.1f}公里，约{duration_min:.0f}分钟"
        elif distance_km is not None:
            summary = f"预计{distance_km:.1f}公里"
        elif duration_min is not None:
            summary = f"预计约{duration_min:.0f}分钟"
        return FinalAnswer(
            recommendations=[
                {
                    "type": "note",
                    "title": "路线建议",
                    "reason": summary,
                }
            ],
            followups=["需要换一种出行方式吗？", "是否需要查看途经餐厅？"],
            warnings=[],
        ).model_dump()
    return None


@register_agent
def _smart_eats_agent() -> AgentConfig:
    return create_agent_config(
        name="smart_eats",
        scene="chat",
        tool_names=[
            "get_weather",
            "get_fridge_items",
            "search_recipes",
            "search_restaurants",
            "plan_route",
            "get_ip_location",
            "geocode_location",
        ],
        max_steps=4,
        system_prompt_builder=smart_system_prompt,
        writer_prompt_builder=default_writer_prompt,
        tool_result_handler=_tool_result_handler,
    )

from __future__ import annotations

from app.agent.agents.base import default_system_prompt, default_writer_prompt
from app.agent.schemas import FinalAnswer
from app.agent.state import ChatState
from app.agent.agent_registry import AgentConfig, create_agent_config, register_agent


def _tool_result_handler(state: ChatState, tool_name: str, result: object) -> dict | None:
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
        cards = []
        for item in result[:3]:
            cards.append(
                {
                    "type": "recipe",
                    "title": item.get("title"),
                    "reason": "适合在家做",
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
def _chat_agent() -> AgentConfig:
    return create_agent_config(
        name="chat",
        scene="chat",
        tool_names=["get_weather", "search_recipes", "search_restaurants"],
        max_steps=4,
        system_prompt_builder=default_system_prompt,
        writer_prompt_builder=default_writer_prompt,
        tool_result_handler=_tool_result_handler,
    )

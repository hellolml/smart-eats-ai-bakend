from __future__ import annotations

from typing import Any

from app.agent.agents.base import default_writer_prompt
from app.common.config import settings
from app.agent.schemas import FinalAnswer
from app.agent.state import ChatState
from app.agent.agent_registry import AgentConfig, create_agent_config, register_agent


def today_system_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are a food decision planner for today's meal. "
        "Prefer short tool usage and produce a concise final answer. "
        "Return the next action in strict JSON schema (tool or final). "
        "Format must be exactly:\n"
        "{\n"
        "  \"type\": \"tool\",\n"
        "  \"name\": \"<tool_name>\",\n"
        "  \"args\": {\"key\": \"value\"}\n"
        "}\n"
        "or\n"
        "{\n"
        "  \"type\": \"final\",\n"
        "  \"answer\": {\n"
        "    \"recommendations\": [],\n"
        "    \"followups\": [],\n"
        "    \"warnings\": []\n"
        "  }\n"
        "}\n"
        f"Always respond in {settings.DEFAULT_LANGUAGE}.\n"
        f"Context: {payload}"
    )


def today_writer_prompt(final_json: dict[str, Any]) -> str:
    return (
        "You are a concise food advisor. Summarize the JSON into a short suggestion. "
        f"Answer JSON: {final_json}"
    )


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
                    "reason": "适合今天吃",
                    "calories": item.get("calories"),
                    "time": item.get("time") or item.get("cook_time_min"),
                    "tags": item.get("tags") or [],
                    "image_url": item.get("image_url"),
                }
            )
        return FinalAnswer(
            recommendations=cards,
            followups=["要不要更清淡？", "偏好主食还是蔬菜多一点？"],
            warnings=[],
        ).model_dump()
    return None


@register_agent
def _today_agent() -> AgentConfig:
    return create_agent_config(
        name="today",
        scene="today_decision",
        tool_names=["get_weather", "search_recipes", "search_restaurants"],
        max_steps=2,
        system_prompt_builder=today_system_prompt,
        writer_prompt_builder=today_writer_prompt,
        tool_result_handler=_tool_result_handler,
    )

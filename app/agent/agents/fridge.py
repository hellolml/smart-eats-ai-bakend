from __future__ import annotations

from app.agent.agents.base import default_system_prompt, default_writer_prompt
from app.agent.schemas import FinalAnswer
from app.agent.state import ChatState
from app.agent.agent_registry import AgentConfig, create_agent_config, register_agent


def _tool_result_handler(state: ChatState, tool_name: str, result: object) -> dict | None:
    if tool_name != "search_recipes" or not isinstance(result, list):
        return None
    cards = []
    for item in result[:3]:
        cards.append(
            {
                "type": "recipe",
                "title": item.get("title"),
                "reason": "根据冰箱食材推荐",
                "calories": item.get("calories"),
                "time": item.get("time") or item.get("cook_time_min"),
                "tags": item.get("tags") or [],
                "image_url": item.get("image_url"),
            }
        )
    return FinalAnswer(
        recommendations=cards,
        followups=["需要我根据现有食材再优化吗？"],
        warnings=[],
    ).model_dump()


@register_agent
def _fridge_agent() -> AgentConfig:
    return create_agent_config(
        name="fridge",
        scene="fridge",
        tool_names=["search_recipes"],
        max_steps=3,
        system_prompt_builder=default_system_prompt,
        writer_prompt_builder=default_writer_prompt,
        tool_result_handler=_tool_result_handler,
    )

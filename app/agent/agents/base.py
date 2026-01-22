from __future__ import annotations

import json
from typing import Any

from app.agent.schemas import AgentAction, AgentActionModel

from app.common.config import settings


def default_system_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are a planner for a SmartEats agent. "
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
        "If tool, choose from allowed tools and provide valid args. "
        f"Context: {payload}"
    )


def default_writer_prompt(final_json: dict[str, Any]) -> str:
    return (
        f"You are a friendly assistant. Respond in {settings.DEFAULT_LANGUAGE}. "
        "Convert the JSON answer into a short reply. "
        "If recommendations is a list, mention every item in order and do not omit any. "
        f"Answer JSON: {final_json}"
    )


def _normalize_final_answer(answer: Any) -> dict[str, Any] | None:
    if not isinstance(answer, dict):
        return None
    recs = answer.get("recommendations", [])
    if isinstance(recs, list) and recs:
        if all(isinstance(item, str) for item in recs):
            answer = dict(answer)
            answer["recommendations"] = [
                {"type": "note", "title": item, "reason": None} for item in recs
            ]
        elif all(isinstance(item, dict) for item in recs):
            mapped: list[dict[str, Any]] = []
            for item in recs:
                if "type" in item:
                    mapped.append(item)
                    continue
                if "cook_time_min" in item or "calories" in item or "image_url" in item:
                    mapped.append(
                        {
                            "type": "recipe",
                            "title": item.get("title"),
                            "reason": item.get("reason"),
                            "calories": item.get("calories"),
                            "time": item.get("time") or item.get("cook_time_min"),
                            "tags": item.get("tags") or [],
                            "image_url": item.get("image_url"),
                        }
                    )
                    continue
                if "rating" in item or "price" in item or "geo" in item:
                    mapped.append(
                        {
                            "type": "restaurant",
                            "title": item.get("title") or item.get("name"),
                            "reason": item.get("reason"),
                            "rating": item.get("rating"),
                            "price": item.get("price"),
                            "tags": item.get("tags") or [],
                            "geo": item.get("geo"),
                        }
                    )
                    continue
                mapped.append(
                    {
                        "type": "note",
                        "title": item.get("title") or item.get("name") or "",
                        "reason": item.get("reason"),
                    }
                )
            answer = dict(answer)
            answer["recommendations"] = mapped
    if "followups" in answer and not isinstance(answer.get("followups"), list):
        answer["followups"] = []
    if "warnings" in answer and not isinstance(answer.get("warnings"), list):
        answer["warnings"] = []
    return answer


def normalize_action_from_raw(content: str) -> AgentAction | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and "tool" in data and "args" in data:
        payload = {"type": "tool", "name": data.get("tool"), "args": data.get("args", {})}
        return AgentActionModel.model_validate(payload).root
    if isinstance(data, dict) and "final" in data:
        answer = _normalize_final_answer(data.get("final"))
        payload = {"type": "final", "answer": answer or data.get("final")}
        return AgentActionModel.model_validate(payload).root
    if isinstance(data, dict) and data.get("type") == "final" and "answer" in data:
        answer = _normalize_final_answer(data.get("answer"))
        payload = {"type": "final", "answer": answer or data.get("answer")}
        return AgentActionModel.model_validate(payload).root
    return None

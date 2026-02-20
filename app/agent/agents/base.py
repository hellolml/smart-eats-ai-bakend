from __future__ import annotations

import json
import re
from typing import Any

from app.agent.schemas import AgentAction, AgentActionModel

from app.common.config import settings


def default_system_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are a planner for a SmartEats agent. "
        "Return the next action in strict <tool_calls> (multiple tools allowed) or final JSON. "
        "Format must be exactly:\n"
        "<tool_calls>[{\"tool_name\": {\"param\": \"value\"}}]</tool_calls>\n"
        "Example:\n"
        "<tool_calls>[{\"tool_a\": {}}, {\"tool_b\": {\"query\": \"example\"}}]</tool_calls>\n"
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


def _normalize_tool_item(item: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(item, dict):
        return None
    # 兼容格式A: {"search_restaurants": {...}}
    if len(item) == 1:
        name, params = next(iter(item.items()))
        if isinstance(name, str) and isinstance(params, dict):
            return {name: params}
    # 兼容格式B: {"tool_name":"search_restaurants", "args": {...}}
    tool_name = item.get("tool_name")
    args = item.get("args")
    if isinstance(tool_name, str) and isinstance(args, dict):
        return {tool_name: args}
    return None


def _parse_tool_calls(raw: str) -> list[dict[str, dict[str, Any]]] | None:
    match = re.search(r"<tool_calls>(.*?)</tool_calls>", raw, re.DOTALL)
    if not match:
        return None
    payload = match.group(1).strip()
    if not payload:
        return []

    # 兼容格式1：<tool_calls>[{"tool": {...}}]</tool_calls>
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        calls: list[dict[str, dict[str, Any]]] = []
        for item in data:
            normalized = _normalize_tool_item(item)
            if not normalized:
                return None
            calls.append(normalized)
        return calls

    # 兼容格式2：<tool_calls><tool_call>{...}</tool_call>...</tool_calls>
    blocks = re.findall(r"<tool_call>(.*?)</tool_call>", payload, re.DOTALL)
    if not blocks:
        return None
    calls: list[dict[str, dict[str, Any]]] = []
    for block in blocks:
        chunk = block.strip()
        if not chunk:
            continue
        try:
            item = json.loads(chunk)
        except json.JSONDecodeError:
            return None
        normalized = _normalize_tool_item(item)
        if not normalized:
            return None
        calls.append(normalized)
    return calls


def normalize_action_from_raw(content: str) -> AgentAction | None:
    tool_calls = _parse_tool_calls(content)
    if tool_calls is not None:
        payload = {"type": "tool_calls", "calls": tool_calls}
        return AgentActionModel.model_validate(payload).root
    
    # 去除 markdown code block 包装
    cleaned = content.strip()
    if cleaned.startswith("```"):
        # 移除开头的 ```json 或 ``` 行
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除结尾的 ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 不是 JSON：剥离潜在工具片段后按纯文本 final 处理，避免工具标记泄露到用户侧
        cleaned_text = re.sub(r"<tool_calls>[\s\S]*?</tool_calls>", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned_text:
            answer = {
                "recommendations": [
                    {"type": "note", "title": cleaned_text, "reason": None}
                ],
                "followups": [],
                "warnings": [],
            }
            payload = {"type": "final", "answer": answer}
            return AgentActionModel.model_validate(payload).root
        return None
    if isinstance(data, dict) and data.get("type") == "tool_calls" and "calls" in data:
        return AgentActionModel.model_validate(data).root
    if isinstance(data, dict) and data.get("type") == "final" and "answer" in data:
        answer = _normalize_final_answer(data.get("answer"))
        payload = {"type": "final", "answer": answer or data.get("answer")}
        return AgentActionModel.model_validate(payload).root
    return None

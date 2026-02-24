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
    # 兼容格式C: {"tool_name":"search_restaurants", "query":"麻辣烫", "location":{...}}
    if isinstance(tool_name, str):
        derived_args = {k: v for k, v in item.items() if k != "tool_name"}
        if isinstance(derived_args, dict):
            return {tool_name: derived_args}
    return None


def _parse_tool_calls(raw: str) -> list[dict[str, dict[str, Any]]] | None:
    stripped = raw.strip()
    if not stripped:
        return None

    if "<tool_calls" in stripped.lower() and not stripped.lower().startswith("<tool_calls"):
        return None

    match = re.fullmatch(r"<tool_calls>(.*?)</tool_calls>", stripped, re.DOTALL | re.IGNORECASE)
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
    # 以及格式3：<tool_call><tool_name>...</tool_name><args>{...}</args></tool_call>
    blocks = re.findall(r"<tool_call>(.*?)</tool_call>", payload, re.DOTALL)
    if not blocks:
        return None
    calls: list[dict[str, dict[str, Any]]] = []
    for block in blocks:
        chunk = block.strip()
        if not chunk:
            continue

        # 先尝试整块 JSON
        try:
            item = json.loads(chunk)
        except json.JSONDecodeError:
            item = None

        if item is not None:
            normalized = _normalize_tool_item(item)
            if not normalized:
                return None
            calls.append(normalized)
            continue

        # 再尝试 XML 子标签格式
        name_match = re.search(r"<tool_name>(.*?)</tool_name>", chunk, re.DOTALL)
        args_match = re.search(r"<args>(.*?)</args>", chunk, re.DOTALL)
        if not name_match:
            return None
        tool_name = name_match.group(1).strip()
        args_payload = args_match.group(1).strip() if args_match else "{}"
        try:
            args = json.loads(args_payload) if args_payload else {}
        except json.JSONDecodeError:
            return None
        if not isinstance(tool_name, str) or not tool_name or not isinstance(args, dict):
            return None
        calls.append({tool_name: args})
    return calls


def _strip_private_reasoning(text: str) -> str:
    # 永远不要把模型思考内容暴露给用户
    stripped = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text, flags=re.IGNORECASE)
    stripped = re.sub(r"<think>[\s\S]*?</think>", "", stripped, flags=re.IGNORECASE)
    return stripped


def normalize_action_from_raw(content: str) -> AgentAction | None:
    content = _strip_private_reasoning(content)
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
        # 容错：模型偶发只吐出残缺 tool_calls 标签（如 "<tool_calls>"），避免后续抛错走 fallback
        lowered = cleaned.lower()
        if "<tool_calls" in lowered and "</tool_calls>" not in lowered:
            answer = {
                "recommendations": [
                    {
                        "type": "note",
                        "title": "我这边刚刚解析步骤时出了点小问题，你再说一次想吃的菜名，我直接给你做法。",
                        "reason": "planner_output_incomplete",
                    }
                ],
                "followups": [],
                "warnings": [],
            }
            payload = {"type": "final", "answer": answer}
            return AgentActionModel.model_validate(payload).root

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

    # 容错：部分模型会直接返回裸 answer（无 type/answer wrapper）
    if isinstance(data, dict) and all(k in data for k in ("recommendations", "followups", "warnings")):
        answer = _normalize_final_answer(data)
        payload = {"type": "final", "answer": answer or data}
        return AgentActionModel.model_validate(payload).root
    return None

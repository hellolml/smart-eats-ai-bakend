from __future__ import annotations

import json
import re
from typing import Any


def strip_private_reasoning(text: str) -> str:
    stripped = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text, flags=re.IGNORECASE)
    return re.sub(r"<think>[\s\S]*?</think>", "", stripped, flags=re.IGNORECASE)


def strip_markdown_code_block(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def strip_control_blocks(text: str) -> str:
    return re.sub(r"<tool_calls[\s\S]*?</tool_calls>", "", text, flags=re.IGNORECASE).strip()


def normalize_final_answer(answer: Any) -> dict[str, Any] | None:
    if not isinstance(answer, dict):
        return None
    normalized = dict(answer)
    recs = normalized.get("recommendations", [])
    if isinstance(recs, list) and recs:
        if all(isinstance(item, str) for item in recs):
            normalized["recommendations"] = [
                {"type": "note", "title": item, "reason": None} for item in recs
            ]
        elif all(isinstance(item, dict) for item in recs):
            normalized["recommendations"] = [
                item
                if "type" in item
                else {
                    "type": "note",
                    "title": item.get("title") or item.get("name") or "",
                    "reason": item.get("reason"),
                }
                for item in recs
            ]
    if "followups" in normalized and not isinstance(normalized.get("followups"), list):
        normalized["followups"] = []
    if "warnings" in normalized and not isinstance(normalized.get("warnings"), list):
        normalized["warnings"] = []
    normalized.setdefault("recommendations", [])
    normalized.setdefault("followups", [])
    normalized.setdefault("warnings", [])
    return normalized


def note_final(title: str, *, reason: str | None = None, followups: list[str] | None = None) -> dict[str, Any]:
    return {
        "recommendations": [{"type": "note", "title": title, "reason": reason}],
        "followups": list(followups or []),
        "warnings": [],
    }


def fallback_final() -> dict[str, Any]:
    return note_final(
        "抱歉，我暂时没能完成这个请求。",
        reason="fallback",
        followups=["可以换个说法试试吗？"],
    )


def final_json_from_text(content: str) -> dict[str, Any]:
    cleaned = strip_markdown_code_block(strip_private_reasoning(content or ""))
    if not cleaned:
        return note_final("好的。", reason="direct_text_response")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        text = strip_control_blocks(cleaned)
        if not text:
            return fallback_final()
        return note_final(text, reason=None)

    if isinstance(data, dict) and data.get("type") == "final" and "answer" in data:
        return normalize_final_answer(data.get("answer")) or fallback_final()
    if isinstance(data, dict) and all(key in data for key in ("recommendations", "followups", "warnings")):
        return normalize_final_answer(data) or fallback_final()
    return note_final(cleaned, reason="direct_text_response")

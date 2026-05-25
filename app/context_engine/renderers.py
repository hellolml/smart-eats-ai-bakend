from __future__ import annotations

from typing import Any


SEGMENT_SUMMARY_SCOPE_NOTE = (
    "This summary covers older middle events only. "
    "Newer raw messages after this summary are authoritative."
)


def render_condensation_summary(summary_json: dict[str, Any]) -> str:
    segment_summary = str(summary_json.get("segment_summary") or summary_json.get("summary") or "").strip()
    sections: list[str] = [
        '<conversation_summary scope="historical_middle_segment">',
        SEGMENT_SUMMARY_SCOPE_NOTE,
        "",
        "Segment summary:",
        segment_summary or "This older segment was compressed.",
    ]
    labels = [
        ("user_goals", "User goals"),
        ("stable_preferences", "Stable preferences"),
        ("decisions", "Decisions"),
        ("tool_results", "Tool results"),
        ("open_questions_at_segment_end", "Open questions at segment end"),
        ("task_state_at_segment_end", "Task state at segment end"),
        ("important_entities", "Important entities"),
        ("do_not_repeat", "Do not repeat"),
    ]
    for key, label in labels:
        values = _list(summary_json.get(key))
        if not values:
            continue
        sections.extend(["", f"{label}:"])
        sections.extend(f"- {value}" for value in values)
    sections.append("</conversation_summary>")
    return "\n".join(sections)


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []

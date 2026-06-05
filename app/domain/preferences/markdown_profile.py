from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.config import settings
from app.domain.preferences.service import extract_preferences_from_text

_JSON_START = "```json"
_JSON_END = "```"
_MAX_EVIDENCE = 12

_CUISINE_TERMS = (
    "川菜",
    "湘菜",
    "粤菜",
    "日料",
    "韩餐",
    "火锅",
    "串串",
    "烧烤",
    "烤肉",
    "面食",
    "米饭",
    "粉面",
    "小吃",
    "甜品",
    "咖啡",
    "茶餐厅",
    "西餐",
    "东南亚菜",
    "清真",
    "素食",
    "家常菜",
    "快餐",
)

_SCENE_TERMS = {
    "外卖": ("外卖", "点外卖"),
    "附近堂食": ("附近", "周边", "堂食", "餐厅", "饭店"),
    "在家做饭": ("在家做", "自己做", "菜谱", "食材", "冰箱"),
    "旅行餐饮": ("旅行", "当地美食", "攻略", "行程"),
}


def default_preference_profile(user_id: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "version": 1,
        "user_id": user_id,
        "likes": [],
        "dislikes": [],
        "allergens": [],
        "diet_goal": None,
        "budget_range": None,
        "spice_level": None,
        "dining_scenes": [],
        "evidence": [],
        "confidence": 0.0,
        "created_at": now,
        "updated_at": now,
    }


async def ensure_user_preference_file(user_id: str | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    path = _profile_path(user_id)
    if path.exists():
        return await read_user_preference_profile(user_id)
    profile = default_preference_profile(user_id)
    _write_profile(path, profile)
    return profile


async def read_user_preference_profile(user_id: str | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    path = _profile_path(user_id)
    if not path.exists():
        return await ensure_user_preference_file(user_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return default_preference_profile(user_id)
    parsed = _parse_profile_json(raw)
    if not parsed:
        parsed = default_preference_profile(user_id)
    parsed.setdefault("user_id", user_id)
    parsed.setdefault("version", 1)
    parsed.setdefault("evidence", [])
    parsed.setdefault("likes", [])
    parsed.setdefault("dislikes", [])
    parsed.setdefault("allergens", [])
    return parsed


async def update_user_preference_profile(
    user_id: str | None,
    *,
    user_text: str | None = None,
    decision_result: dict[str, Any] | None = None,
    source: str = "conversation",
) -> dict[str, Any] | None:
    if not user_id:
        return None
    profile = deepcopy(await read_user_preference_profile(user_id) or default_preference_profile(user_id))
    before = json.dumps(profile, ensure_ascii=False, sort_keys=True)
    text_parts = [user_text or ""]
    if isinstance(decision_result, dict):
        decision = decision_result.get("decision") if isinstance(decision_result.get("decision"), dict) else {}
        text_parts.append(str(decision.get("title") or ""))
        raw = decision.get("raw") if isinstance(decision.get("raw"), dict) else {}
        text_parts.append(str(raw.get("name") or raw.get("title") or ""))
        tags = raw.get("tags")
        if isinstance(tags, list):
            text_parts.extend(str(item) for item in tags)
        elif isinstance(tags, str):
            text_parts.append(tags)
    combined = " ".join(part for part in text_parts if part).strip()

    extracted = extract_preferences_from_text(user_text or "")
    _merge_list(profile, "dislikes", extracted.get("dislikes") or [])
    _merge_list(profile, "allergens", extracted.get("allergens") or [])
    for field in ("diet_goal", "budget_range", "spice_level"):
        if extracted.get(field) is not None:
            profile[field] = extracted[field]

    likes = _extract_likes(combined)
    _merge_list(profile, "likes", likes)
    _merge_list(profile, "dining_scenes", _extract_scenes(combined))
    if likes or extracted:
        _append_evidence(profile, source=source, text=user_text or combined, changes={
            "likes": likes,
            "extracted": {key: value for key, value in extracted.items() if key != "requires_sensitive_confirmation"},
        })

    if before != json.dumps(profile, ensure_ascii=False, sort_keys=True):
        profile["confidence"] = min(0.95, max(float(profile.get("confidence") or 0.0), 0.55))
        profile["updated_at"] = _now_iso()
        _write_profile(_profile_path(user_id), profile)
    return profile


def build_preference_context(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    summary = []
    likes = _clean_list(profile.get("likes"))
    dislikes = _clean_list(profile.get("dislikes"))
    allergens = _clean_list(profile.get("allergens"))
    scenes = _clean_list(profile.get("dining_scenes"))
    if likes:
        summary.append(f"偏好：{', '.join(likes[:8])}")
    if dislikes:
        summary.append(f"不喜欢/忌口：{', '.join(dislikes[:8])}")
    if allergens:
        summary.append(f"过敏/敏感项：{', '.join(allergens[:8])}")
    if profile.get("spice_level") is not None:
        summary.append(f"辣度：{profile.get('spice_level')}")
    if profile.get("budget_range"):
        summary.append(f"预算：{profile.get('budget_range')}")
    if scenes:
        summary.append(f"常见场景：{', '.join(scenes[:5])}")
    return {
        "summary": "；".join(summary) if summary else "暂无明确饮食偏好，可在对话中逐步形成。",
        "profile": {
            "likes": likes,
            "dislikes": dislikes,
            "allergens": allergens,
            "diet_goal": profile.get("diet_goal"),
            "budget_range": profile.get("budget_range"),
            "spice_level": profile.get("spice_level"),
            "dining_scenes": scenes,
            "confidence": profile.get("confidence"),
            "updated_at": profile.get("updated_at"),
        },
        "markdown": render_preference_markdown(profile, include_json=False),
    }


def render_preference_markdown(profile: dict[str, Any], *, include_json: bool = True) -> str:
    profile = deepcopy(profile)
    lines: list[str] = ["# 用户饮食偏好文件", ""]
    if include_json:
        lines.extend([_JSON_START, json.dumps(profile, ensure_ascii=False, indent=2), _JSON_END, ""])
    lines.extend(
        [
            "## 摘要",
            f"- 喜欢/倾向：{_display_list(profile.get('likes'))}",
            f"- 不喜欢/忌口：{_display_list(profile.get('dislikes'))}",
            f"- 过敏/敏感项：{_display_list(profile.get('allergens'))}",
            f"- 饮食目标：{profile.get('diet_goal') or '暂无'}",
            f"- 预算倾向：{profile.get('budget_range') or '暂无'}",
            f"- 辣度偏好：{profile.get('spice_level') if profile.get('spice_level') is not None else '暂无'}",
            f"- 常见用餐场景：{_display_list(profile.get('dining_scenes'))}",
            "",
            "## 证据记录",
        ]
    )
    evidence = profile.get("evidence") if isinstance(profile.get("evidence"), list) else []
    if not evidence:
        lines.append("- 暂无，由吃点啥对话逐步积累。")
    else:
        for item in evidence[-_MAX_EVIDENCE:]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('at') or ''} [{item.get('source') or 'conversation'}] {item.get('text') or ''}".strip())
    lines.extend(["", f"_updated_at: {profile.get('updated_at') or _now_iso()}_"])
    return "\n".join(lines).strip() + "\n"


def _profile_path(user_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", user_id)
    root = Path(settings.USER_PREFERENCE_MD_DIR)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe_id}.md"


def _write_profile(path: Path, profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_preference_markdown(profile), encoding="utf-8")


def _parse_profile_json(raw: str) -> dict[str, Any] | None:
    start = raw.find(_JSON_START)
    if start < 0:
        return None
    body_start = start + len(_JSON_START)
    end = raw.find(_JSON_END, body_start)
    if end < 0:
        return None
    try:
        parsed = json.loads(raw[body_start:end].strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_likes(text: str) -> list[str]:
    if not text:
        return []
    likes = [term for term in _CUISINE_TERMS if term in text]
    for token in re.findall(r"(?:喜欢|爱吃|想吃|偏好)([^，。！？；,!.?]{1,10})", text):
        token = token.strip()
        if token and not any(prefix in token for prefix in ("不", "讨厌")):
            likes.append(token)
    return _clean_list(likes)


def _extract_scenes(text: str) -> list[str]:
    if not text:
        return []
    scenes: list[str] = []
    for label, terms in _SCENE_TERMS.items():
        if any(term in text for term in terms):
            scenes.append(label)
    return scenes


def _merge_list(profile: dict[str, Any], field: str, values: list[Any]) -> None:
    existing = _clean_list(profile.get(field))
    for value in values:
        text = str(value or "").strip()
        if text and text not in existing:
            existing.append(text)
    profile[field] = existing[:30]


def _append_evidence(profile: dict[str, Any], *, source: str, text: str, changes: dict[str, Any]) -> None:
    evidence = profile.get("evidence") if isinstance(profile.get("evidence"), list) else []
    compact = re.sub(r"\s+", " ", text).strip()
    evidence.append({
        "at": _now_iso(),
        "source": source,
        "text": compact[:120],
        "changes": changes,
    })
    profile["evidence"] = evidence[-_MAX_EVIDENCE:]


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _display_list(value: Any) -> str:
    items = _clean_list(value)
    return "、".join(items) if items else "暂无"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

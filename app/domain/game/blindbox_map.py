from __future__ import annotations

BLINDBOX_RESULT_MAP: dict[str, dict[str, str]] = {
    "noodles": {"id": "noodles", "name_cn": "拉面", "emoji": "🍜"},
    "dumplings": {"id": "dumplings", "name_cn": "饺子", "emoji": "🥟"},
    "salad": {"id": "salad", "name_cn": "沙拉", "emoji": "🥗"},
    "soup": {"id": "soup", "name_cn": "汤品", "emoji": "🍲"},
    "rice bowl": {"id": "rice_bowl", "name_cn": "盖饭", "emoji": "🍚"},
    "hotpot": {"id": "hotpot", "name_cn": "火锅", "emoji": "🍲"},
    "sushi": {"id": "sushi", "name_cn": "寿司", "emoji": "🍣"},
    "burger": {"id": "burger", "name_cn": "汉堡", "emoji": "🍔"},
    "ramen": {"id": "ramen", "name_cn": "拉面", "emoji": "🍜"},
}


def map_blindbox_result(result: str) -> dict[str, str]:
    key = (result or "").strip().lower()
    mapped = BLINDBOX_RESULT_MAP.get(key)
    if mapped:
        return mapped
    return {"id": key or "unknown", "name_cn": result or "未知", "emoji": "🍽️"}

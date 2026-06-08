from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TravelWorkflowState(BaseModel):
    model_config = ConfigDict(extra="allow")

    state: str | None = None
    previous_final_json: dict[str, Any] | None = None
    trip_meta: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    places: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    failed_places: list[dict[str, Any]] = Field(default_factory=list)
    food_items: list[dict[str, Any]] = Field(default_factory=list)
    candidate_groups: dict[str, Any] | None = None
    itinerary: dict[str, Any] | None = None
    map: dict[str, Any] | None = None
    raw_text: str | None = None
    refresh_sources: bool = False
    stale_artifacts: dict[str, Any] | None = None

    @classmethod
    def from_final_json(cls, latest: dict[str, Any] | None) -> "TravelWorkflowState":
        if not isinstance(latest, dict):
            return cls()
        payload = {
            key: latest.get(key)
            for key in (
                "state",
                "trip_meta",
                "sources",
                "places",
                "candidates",
                "failed_places",
                "food_items",
                "candidate_groups",
                "itinerary",
                "map",
                "raw_text",
            )
            if latest.get(key) not in (None, [], {})
        }
        payload["previous_final_json"] = latest
        return cls.model_validate(payload)

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude_defaults=True)


class TravelSourceIngestionService:
    @staticmethod
    def ingest_text_request(message: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            return payload
        extracted = _extract_text_request_payload(text)
        if not extracted:
            return payload

        next_payload = dict(payload)
        trip_meta = dict(next_payload.get("trip_meta") if isinstance(next_payload.get("trip_meta"), dict) else {})
        trip_meta.update(
            {
                key: value
                for key, value in (extracted.get("trip_meta") or {}).items()
                if value not in (None, "", [], {})
            }
        )
        if trip_meta:
            next_payload["trip_meta"] = trip_meta
        places = _merge_place_lists(next_payload.get("extracted_places") or next_payload.get("places"), extracted.get("extracted_places") or [])
        if places:
            next_payload["extracted_places"] = places
        excluded = _merge_place_lists(next_payload.get("excluded_places"), extracted.get("excluded_places") or [])
        if excluded:
            next_payload["excluded_places"] = excluded
        if trip_meta or places or excluded:
            next_payload.setdefault("state", "ingesting_content")
        return next_payload

    @staticmethod
    def has_new_attachments(payload: dict[str, Any]) -> bool:
        value = payload.get("new_attachments")
        return isinstance(value, list) and any(isinstance(item, dict) for item in value)

    @staticmethod
    def mark_refresh_sources(payload: dict[str, Any], latest: dict[str, Any] | None) -> dict[str, Any]:
        next_payload = dict(payload)
        previous = latest if isinstance(latest, dict) else next_payload.get("previous_final_json")
        if isinstance(previous, dict):
            if previous.get("itinerary") and not next_payload.get("previous_itinerary"):
                next_payload["previous_itinerary"] = previous.get("itinerary")
            if previous.get("map") and not next_payload.get("previous_map"):
                next_payload["previous_map"] = previous.get("map")
        next_payload["state"] = "ingesting_content"
        next_payload["refresh_sources"] = True
        next_payload["stale_artifacts"] = {
            "itinerary": bool(next_payload.get("previous_itinerary")),
            "map": bool(next_payload.get("previous_map")),
            "reason": "new_attachments",
        }
        next_payload.pop("itinerary", None)
        next_payload.pop("map", None)
        return next_payload


class TravelCandidateService:
    @staticmethod
    def context_from_final_json(latest: dict[str, Any] | None) -> dict[str, Any]:
        return TravelWorkflowState.from_final_json(latest).as_payload()

    @staticmethod
    def apply_revision_from_message(
        message: str | None,
        payload: dict[str, Any],
        *,
        latest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            return payload
        revision = _travel_revision_from_message(text)
        if not revision:
            return payload

        latest_payload = latest if isinstance(latest, dict) else {}
        previous_trip_meta = latest_payload.get("trip_meta") if isinstance(latest_payload.get("trip_meta"), dict) else {}
        next_payload = dict(payload)
        trip_meta = dict(next_payload.get("trip_meta") if isinstance(next_payload.get("trip_meta"), dict) else {})
        trip_meta.update({key: value for key, value in revision.get("trip_meta", {}).items() if value not in (None, "", [], {})})
        if not trip_meta and previous_trip_meta:
            trip_meta = dict(previous_trip_meta)
        next_payload["trip_meta"] = trip_meta
        next_payload["state"] = "ingesting_content"
        next_payload["refresh_sources"] = True
        next_payload["revision"] = {
            "type": "trip_revision",
            **revision,
        }
        next_payload["stale_artifacts"] = {
            "candidates": bool(latest_payload.get("candidates") or payload.get("candidates")),
            "failed_places": bool(latest_payload.get("failed_places") or payload.get("failed_places")),
            "itinerary": bool(latest_payload.get("itinerary") or payload.get("itinerary")),
            "map": bool(latest_payload.get("map") or payload.get("map")),
            "reason": "trip_revision",
        }
        _preserve_previous_artifact(next_payload, "candidates", latest_payload)
        _preserve_previous_artifact(next_payload, "failed_places", latest_payload)
        _preserve_previous_artifact(next_payload, "itinerary", latest_payload)
        _preserve_previous_artifact(next_payload, "map", latest_payload)
        for key in ("candidates", "confirmed_candidates", "failed_places", "candidate_groups", "itinerary", "map"):
            next_payload.pop(key, None)
        retained_places = revision.get("retained_places")
        if isinstance(retained_places, list) and retained_places:
            next_payload["extracted_places"] = retained_places
        excluded_places = revision.get("excluded_places")
        if isinstance(excluded_places, list) and excluded_places:
            next_payload["excluded_places"] = _merge_place_lists(next_payload.get("excluded_places"), excluded_places)
        return next_payload

    @staticmethod
    def infer_action_from_message(
        message: str | None,
        *,
        latest: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str | None:
        text = str(message or "").strip()
        if not text:
            return None
        latest = latest if isinstance(latest, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        state = str(payload.get("state") or latest.get("state") or "")
        has_candidates = bool(payload.get("candidates") or latest.get("candidates"))
        has_itinerary = isinstance(payload.get("itinerary") or latest.get("itinerary"), dict)

        if any(token in text for token in ("生成地图", "生成二维码", "高德地图", "地图二维码")):
            return "generate_map" if has_itinerary or state == "itinerary_generated" else None

        confirm_text = any(token in text for token in ("确认", "可以", "继续", "没问题", "就这样", "下一步"))
        wants_itinerary = any(token in text for token in ("生成行程", "最终每日行程", "每日行程", "继续生成"))
        if (state == "candidates_ready" or has_candidates) and (confirm_text or wants_itinerary):
            return "confirm_candidates"
        if (state == "itinerary_generated" or has_itinerary) and confirm_text:
            return "generate_map"
        return None


class TravelItineraryService:
    @staticmethod
    def has_itinerary(payload: dict[str, Any]) -> bool:
        return isinstance(payload.get("itinerary"), dict) or isinstance(payload.get("previous_itinerary"), dict)


class TravelMapService:
    @staticmethod
    def has_map(payload: dict[str, Any]) -> bool:
        return isinstance(payload.get("map"), dict) or isinstance(payload.get("previous_map"), dict)


def _preserve_previous_artifact(payload: dict[str, Any], key: str, latest: dict[str, Any]) -> None:
    previous_key = f"previous_{key}"
    if previous_key not in payload and latest.get(key) not in (None, [], {}):
        payload[previous_key] = latest.get(key)


def _travel_revision_from_message(text: str) -> dict[str, Any] | None:
    has_revision_token = any(token in text for token in ("改成", "调整为", "换成", "变成", "不去", "不去了", "只保留", "保留"))
    if not has_revision_token:
        return None
    trip_meta: dict[str, Any] = {}
    destination = _infer_destination_from_message(text)
    if destination:
        trip_meta["destination"] = destination
    days = _parse_day_count(text)
    if days:
        trip_meta["days"] = days
    retained_places = _extract_retained_places(text)
    excluded_places = _extract_excluded_places(text)
    if not trip_meta and not retained_places and not excluded_places:
        return None
    return {
        "trip_meta": trip_meta,
        "retained_places": retained_places,
        "excluded_places": excluded_places,
    }


def _extract_text_request_payload(text: str) -> dict[str, Any]:
    trip_meta = _trip_meta_from_structured_text(text)
    places = _extract_places_from_structured_text(text)
    excluded = _extract_excluded_places(text)
    payload: dict[str, Any] = {}
    if trip_meta:
        payload["trip_meta"] = trip_meta
    if places:
        payload["extracted_places"] = places
    if excluded:
        payload["excluded_places"] = excluded
    return payload


def _trip_meta_from_structured_text(text: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    destination = _structured_field(text, ("目的地", "城市", "旅行地", "旅游地"))
    destination = destination or _infer_destination_from_message(text)
    if destination:
        meta["destination"] = destination
    start_date = _structured_field(text, ("出行时间", "出发时间", "开始时间", "日期"))
    if start_date:
        match = re.search(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}", start_date)
        if match:
            meta["start_date"] = (
                match.group(0)
                .replace("年", "-")
                .replace("月", "-")
                .replace("/", "-")
                .replace(".", "-")
                .rstrip("日")
            )
    days_text = _structured_field(text, ("出行天数", "旅行天数", "游玩天数", "天数"))
    days = _parse_day_count(days_text or text)
    if days:
        meta["days"] = days
    travelers = _structured_field(text, ("出行人数", "旅行人数", "人数"))
    travelers_count = _parse_int_or_chinese_number(travelers or "")
    if travelers_count:
        meta["travelers_count"] = travelers_count
    return meta


def _structured_field(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n\r]+)", text)
        if not match:
            continue
        value = match.group(1).strip(" \t,，。；;")
        value = re.split(r"\s+(?:出行|旅行|游玩|人数|请|希望)", value, maxsplit=1)[0].strip()
        return value or None
    return None


def _extract_places_from_structured_text(text: str) -> list[dict[str, Any]]:
    segments: list[str] = []
    for pattern in (
        r"(?:我想去|想去|景点|地点|想玩的地方)\s*[:：]\s*([^\n\r]+)",
    ):
        segments.extend(re.findall(pattern, text))
    places: list[dict[str, Any]] = []
    for segment in segments:
        segment = _strip_prompt_tail(segment)
        for part in re.split(r"[、,，;；和与及]+", segment):
            name = _clean_place_name(part)
            if name and not _is_revision_noise(name) and not _is_prompt_or_helper_text(name):
                places.append({"name": name, "category": _category_from_text(name), "source": "user_structured_text"})
    return _dedupe_places(places)


def _strip_prompt_tail(segment: str) -> str:
    return re.split(
        r"(?:偏好|请|要求|备注|第二晚|晚上想|晚上|下午|上午|等我确认|等待用户确认|不要直接|生成|输出)\s*[:：]?",
        str(segment or ""),
        maxsplit=1,
    )[0]


def _merge_place_lists(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    existing_items = existing if isinstance(existing, list) else []
    incoming_items = incoming if isinstance(incoming, list) else []
    return _dedupe_places([item for item in [*existing_items, *incoming_items] if isinstance(item, dict)])


def _infer_destination_from_message(text: str) -> str | None:
    for pattern in (
        r"(?:临时)?(?:改成|调整为|换成|变成)\s*([\u4e00-\u9fa5]{2,8})\s*(?:\d+|[一二两三四五六七八九十]+)\s*天",
        r"(?:改成|调整为|换成|变成)\s*([\u4e00-\u9fa5]{2,8})(?:[，。！？；;\s]|$)",
    ):
        match = re.search(pattern, text)
        if match:
            value = _clean_place_name(match.group(1))
            if value:
                return value
    for city in (
        "广州",
        "深圳",
        "杭州",
        "成都",
        "重庆",
        "长沙",
        "南京",
        "苏州",
        "西安",
        "武汉",
        "厦门",
        "青岛",
        "大理",
        "丽江",
        "上海",
        "北京",
    ):
        if city in text and any(token in text for token in ("改成", "调整为", "换成", "变成")):
            return city
    return None


def _parse_day_count(text: str) -> int | None:
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return int(match.group(1))
    match = re.search(r"([一二两三四五六七八九十]+)\s*天", text)
    if not match:
        return None
    value = match.group(1)
    numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十") and len(value) == 2:
        return 10 + numerals.get(value[1], 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return numerals.get(left, 1) * 10 + numerals.get(right, 0)
    return numerals.get(value)


def _parse_int_or_chinese_number(value: str) -> int | None:
    text = str(value or "")
    digit = re.search(r"\d+", text)
    if digit:
        return int(digit.group(0))
    numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2:
        return 10 + numerals.get(text[1], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return numerals.get(left, 1) * 10 + numerals.get(right, 0)
    for char in text:
        if char in numerals:
            return numerals[char]
    return None


def _extract_retained_places(text: str) -> list[dict[str, Any]]:
    segments: list[str] = []
    for pattern in (
        r"只保留([^。！？；;\n\r]+)",
        r"保留([^。！？；;\n\r]+)",
        r"地点(?:是|为|改成|改为)\s*([^。！？；;\n\r]+)",
    ):
        segments.extend(re.findall(pattern, text))
    places: list[dict[str, Any]] = []
    for segment in segments:
        segment = re.split(r"(?:别太赶|不要太赶|轻松|预算|偏好|确认|生成)", segment, maxsplit=1)[0]
        for part in re.split(r"[、,，和与及]+", segment):
            name = _clean_place_name(part)
            if name and not _is_revision_noise(name):
                places.append({"name": name, "category": _category_from_text(name), "source": "user_revision"})
    return _dedupe_places(places)


def _extract_excluded_places(text: str) -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []
    for pattern in (
        r"([^，。！？；;\n\r]{2,20})不去了",
        r"不去(?:的是|的|是)?\s*([^，。！？；;\n\r]{2,20})",
    ):
        for match in re.findall(pattern, text):
            name = _clean_place_name(match)
            if name and not _is_revision_noise(name):
                places.append({"name": name, "source_name": name, "exclude_reason": "用户明确排除"})
    return _dedupe_places(places)


def _clean_place_name(value: Any) -> str:
    text = str(value or "").strip(" \t，。！？；;:：、")
    text = re.sub(r"^(?:地点|景点|只|都|也|再|的是|是|为)", "", text)
    text = re.sub(r"(?:附近|周边|一带)$", "", text)
    text = re.sub(r"\s+", "", text)
    return text[:32]


def _is_revision_noise(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if re.fullmatch(r"(?:\d+|[一二两三四五六七八九十]+)\s*(?:天|晚|日)", text):
        return True
    if re.fullmatch(r"[\u4e00-\u9fa5]{2,8}(?:\d+|[一二两三四五六七八九十]+)天", text):
        return True
    return any(token in text for token in ("别太赶", "轻松", "预算", "偏好", "确认这版")) or _is_prompt_or_helper_text(text)


def _is_prompt_or_helper_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if len(text) > 20 and any(
        token in text
        for token in (
            "我可以",
            "你可以",
            "您可以",
            "请输出",
            "请先输出",
            "请继续",
            "等待用户确认",
            "不要直接",
            "创建计划记录",
            "高德验证POI",
            "排出一份",
            "候选行程",
            "必要提醒",
        )
    ):
        return True
    compact = re.sub(r"[\s，。！？；;:：、]+", "", text)
    return compact in {
        "请输出清晰的候选旅行行程",
        "请先输出候选行程",
        "等待用户确认后由应用层创建计划记录",
        "不要直接操作数据库",
    }


def _category_from_text(name: str) -> str:
    value = str(name or "")
    if any(token in value for token in ("餐厅", "饭店", "火锅", "小吃", "菜", "咖啡", "茶", "酒吧")):
        return "restaurant"
    if any(token in value for token in ("酒店", "民宿", "宾馆")):
        return "hotel"
    if any(token in value for token in ("机场", "车站", "码头", "地铁站")):
        return "transport_hub"
    return "attraction"


def _dedupe_places(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        key = re.sub(r"[\s\u3000（）()【】\[\]·•、,，。.!！?？:：;；\-_\/]+", "", name.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

from __future__ import annotations

from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


class FoodDecisionHooks(BaseSkillHooks):
    def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "system_directive": (
                "当前是吃什么/吃点啥决策场景。不要回答没有美食推荐能力；"
                "你必须调用 food_decision 工具来给出推荐，不允许直接回复文字答案；"
                "用户提到附近、周边或地标时可结合 restaurant_finder。"
            )
        }

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "food_decision":
            return args
        updated = dict(args)
        context = getattr(state, "context", None)
        if isinstance(context, dict):
            location = _extract_location(context)
            if location:
                updated.setdefault("lat", location.get("lat"))
                updated.setdefault("lng", location.get("lng"))
            city = context.get("city")
            if isinstance(city, str) and city.strip():
                updated.setdefault("city", city)
        updated.setdefault("query", getattr(state, "message", None) or "今天吃点啥")
        intent = None
        context = getattr(state, "context", None)
        if isinstance(context, dict):
            intent = context.get("intent")
        updated.setdefault("scene", "cook_home" if intent == "cook_home" else "eat")
        return updated

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if tool_name != "food_decision" or not isinstance(result, dict):
            return None
        if result.get("error"):
            return {
                "recommendations": [
                    {
                        "type": "note",
                        "title": "我还缺少一点位置信息，暂时没法精确推荐附近餐厅。",
                        "reason": str(result.get("error") or "food_decision_failed"),
                    }
                ],
                "followups": ["发我当前城市或附近地标", "重新授权定位后再推荐一次"],
                "warnings": [str(result.get("error"))],
            }
        decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
        title = str(decision.get("title") or "今天就选这个").strip()
        rec_type = str(decision.get("type") or "note").strip()
        reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
        reason = "；".join(str(item).strip() for item in reasons if str(item).strip()) or "基于你的口味和当前场景做了收敛"
        actions = result.get("actions") if isinstance(result.get("actions"), list) else []
        followups = [
            str(item.get("label") or "").strip()
            for item in actions
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
        return {
            "recommendations": [
                {
                    "type": rec_type,
                    "title": title,
                    "reason": reason,
                    "raw": decision,
                }
            ],
            "followups": followups[:3],
            "warnings": [],
            "decision": result,
        }


def _extract_location(context: dict[str, Any]) -> dict[str, float] | None:
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = environment.get("location") if isinstance(environment.get("location"), dict) else context.get("location")
    if not isinstance(location, dict):
        return None
    try:
        lat = float(location.get("lat"))
        lng = float(location.get("lng"))
    except (TypeError, ValueError):
        return None
    if lat == 0 or lng == 0:
        return None
    return {"lat": lat, "lng": lng}

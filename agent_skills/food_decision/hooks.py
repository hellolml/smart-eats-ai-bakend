from __future__ import annotations

from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


class FoodDecisionHooks(BaseSkillHooks):
    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if tool_name != "food_decision" or not isinstance(result, dict) or result.get("error"):
            return None
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

from __future__ import annotations

from typing import Any, Literal

ChatIntent = Literal["route", "food"]
FoodWorkerIntent = Literal["cook_home", "eat_out"]

ROUTE_INTENT_KEYWORDS = ("路线", "导航", "怎么走", "怎么去")

FOOD_INTENT_KEYWORDS = (
    "吃点啥",
    "吃什么",
    "吃的",
    "吃啥",
    "今天吃",
    "晚饭",
    "午饭",
    "早餐",
    "夜宵",
    "外卖",
    "餐厅",
    "饭店",
    "美食",
    "好吃",
    "周边吃",
    "附近吃",
    "附近美食",
    "推荐吃",
    "出去吃",
    "外面吃",
    "去哪吃",
    "换一家",
    "下一家",
    "第二家",
    "第三家",
    "近一点",
    "不辣",
    "做饭",
    "在家做",
    "家里做",
    "菜谱",
    "食谱",
    "冰箱",
    "食材",
    "自己做",
)

COOK_HOME_INTENT_KEYWORDS = ("做饭", "在家做", "家里做", "菜谱", "食谱", "冰箱", "食材", "自己做")
EAT_OUT_INTENT_KEYWORDS = ("吃点啥", "吃什么", "吃的", "吃啥", "今天吃", "饭", "餐", "美食", "外卖", "餐厅", "饭店", "出去吃", "外面吃", "去哪吃")


def infer_chat_intent(message: Any) -> ChatIntent | None:
    text = str(message or "")
    if not text:
        return None
    if any(token in text for token in ROUTE_INTENT_KEYWORDS):
        return "route"
    if any(token in text for token in FOOD_INTENT_KEYWORDS):
        return "food"
    return None


def infer_food_worker_intent(
    message: Any,
    *,
    explicit_intent: str | None = None,
) -> FoodWorkerIntent | None:
    if explicit_intent in {"cook_home", "eat_out"}:
        return explicit_intent
    text = str(message or "")
    if not text:
        return None
    if any(token in text for token in COOK_HOME_INTENT_KEYWORDS):
        return "cook_home"
    if any(token in text for token in EAT_OUT_INTENT_KEYWORDS):
        return "eat_out"
    return None

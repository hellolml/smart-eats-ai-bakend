from __future__ import annotations

from app.agent.intent import infer_chat_intent, infer_food_worker_intent


def test_infer_chat_intent_uses_shared_keywords():
    assert infer_chat_intent("第二家怎么走") == "route"
    assert infer_chat_intent("今天吃点啥") == "food"
    assert infer_chat_intent("冰箱里有鸡蛋") == "food"
    assert infer_chat_intent("随便聊聊") is None


def test_infer_food_worker_intent_respects_explicit_worker_intent():
    assert infer_food_worker_intent("今天吃点啥", explicit_intent="cook_home") == "cook_home"
    assert infer_food_worker_intent("冰箱里有鸡蛋") == "cook_home"
    assert infer_food_worker_intent("自己做") == "cook_home"
    assert infer_food_worker_intent("今天吃点啥") == "eat_out"
    assert infer_food_worker_intent("随便聊聊") is None

---
id: food_assistant
name: Food Assistant
version: 1.0.0
description: Handle food-related conversations, including eating out, home cooking, and quick food decisions.
enabled: true
priority: 90
activation:
  scenes:
    - home_chef
    - restaurant
  intents:
    - food
  keywords:
    - 吃什么
    - 吃点啥
    - 今天吃
    - 晚饭
    - 午饭
    - 早餐
    - 夜宵
    - 出去吃
    - 外面吃
    - 去哪吃
    - 附近
    - 餐厅
    - 饭店
    - 外卖
    - 换一家
    - 下一家
    - 第二家
    - 第三家
    - 近一点
    - 不辣
    - 做饭
    - 在家做
    - 家里做
    - 菜谱
    - 食谱
    - 冰箱
    - 食材
    - 自己做
  min_score: 1
tools:
  allow:
    - food_decision
    - get_fridge_items
    - rag_search_recipes
    - search_recipes
    - get_ip_location
    - geocode_location
    - search_restaurants
    - get_weather
  require_global_allowlist: true
safety:
  can_override_global_rules: false
  allow_external_tools: false
  max_tool_calls_per_turn: 4
context:
  read:
    - user_message
    - history
    - memories
    - cached_location
    - last_restaurants
    - fridge_items
    - food_mode
  write:
    - active_skills
    - food_mode
    - fridge_items
    - last_restaurants
hooks:
  class: hooks.FoodAssistantHooks
---
# Food Assistant Skill

当用户提出任何吃相关请求时，使用本 skill。你需要先在本 skill 内判断子意图，再调用对应工具。

## 子意图

- `eat_out`：用户说出去吃、外面吃、附近餐厅、饭店、外卖、去哪吃、找店、换一家、近一点等。
- `cook_home`：用户说在家做、家里做、做饭、菜谱、食谱、冰箱、食材、自己做等。
- `decide_food`：用户只泛化询问今天吃什么、吃点啥、午饭/晚饭/早餐吃啥，但没有明确在家或外出。
- `clarify`：当前信息不足以判断在家做还是出去吃。

## 行为规则

- 先根据最新用户消息判断子意图；如果最新消息是“换一家”“第二个”“不辣的”“近一点”等追问，结合上下文中的 `food_mode`、`last_restaurants`、`fridge_items` 和工具观察继续处理。
- `eat_out` 模式优先使用已有位置、缓存位置或定位工具，然后调用 `search_restaurants`。缺少位置时先尝试定位；定位仍失败时向用户询问城市、商圈或当前位置。
- `eat_out` 模式禁止把菜谱、家常菜名或 `food_decision` 的 fallback 菜名作为最终答案。没有餐厅结果时，说明缺少位置或建议换商圈/口味。
- `cook_home` 模式优先调用 `get_fridge_items`；需要菜谱时优先调用 `rag_search_recipes`，再考虑 `search_recipes`。
- `decide_food` 模式可以调用 `food_decision` 给出一个明确推荐；最终答复必须说明推荐类型是餐厅、菜谱还是兜底建议。
- `clarify` 模式只问一个问题：你想在家做，还是出去吃？

---
id: food_decision
name: Food Decision
version: 1.0.0
description: Help users quickly decide what to eat by reusing the app decision engine.
enabled: true
priority: 82
activation:
  scenes:
    - chat
    - home_chef
  intents:
    - cook_home
    - eat_out
  keywords:
    - 吃点啥
    - 吃什么
    - 今天吃
    - 晚饭
    - 午饭
    - 早餐
    - 不知道吃
    - 吃的
    - 吃啥
    - 美食
    - 好吃
    - 周边吃
    - 附近吃
    - 推荐吃
  min_score: 4
tools:
  allow:
    - food_decision
  require_global_allowlist: true
safety:
  can_override_global_rules: false
  allow_external_tools: false
  max_tool_calls_per_turn: 2
context:
  read:
    - user_message
    - memories
    - cached_location
  write:
    - active_skills
hooks:
  class: hooks.FoodDecisionHooks
---
# Food Decision Skill

当用户泛化询问“今天吃什么”“吃点啥”“午饭/晚饭怎么选”时，优先调用 `food_decision`，用现有快决策能力给出一个明确推荐。

规则：

- 如果用户明确要在家做饭、冰箱食材或菜谱，允许 `home_chef` 继续补充菜谱细节。
- 如果用户明确要外出吃饭，推荐结果可以是餐厅。
- 不要只输出兜底文案；能调用 `food_decision` 时必须调用。
- 如果用户提到周边、附近、商场、楼宇或地标附近吃什么，必须调用 `food_decision`，必要时配合 `restaurant_finder`。
- 不要把美食请求转成旅行规划或地图服务，也不要建议用户改用外部外卖平台作为最终答复。
- 最终答复给出 1 个主推荐、关键理由和可执行动作。

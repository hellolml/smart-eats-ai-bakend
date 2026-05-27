---
id: restaurant_finder
name: Restaurant Finder
version: 1.0.0
description: Help users find nearby restaurants and choose where to eat out.
enabled: true
priority: 70
activation:
  scenes:
    - restaurant
  intents:
    - eat_out
  keywords:
    - 附近
    - 餐厅
    - 饭店
    - 外面吃
    - 出去吃
    - 去哪吃
    - 外卖
    - 周边
    - 附近吃
    - 附近美食
    - 吃的
  min_score: 1
tools:
  allow:
    - get_ip_location
    - geocode_location
    - search_restaurants
    - get_weather
  require_global_allowlist: true
safety:
  can_override_global_rules: false
  allow_external_tools: false
  max_tool_calls_per_turn: 3
context:
  read:
    - user_message
    - history
    - cached_location
    - last_restaurants
  write:
    - active_skills
hooks:
  class: hooks.RestaurantFinderHooks
---
# Restaurant Finder Skill

当用户想外出吃饭、寻找附近餐厅、比较餐厅或询问去哪吃时，使用本 skill。

行为规则：

- 如果缺少位置，优先利用已有定位上下文；仍缺失时再调用定位或地理编码工具。
- 查询附近餐厅时优先调用 `search_restaurants`。
- 推荐餐厅时给出可执行理由，例如距离、菜系、适合场景或用户偏好匹配。
- `search_restaurants` 返回空结果后，不要重复调用同一工具，应给出换商圈或改为在家做饭的选项。
- 用户确认某家餐厅后，不要继续推荐其他餐厅，转入路线或最终答复。
- 用户泛化询问“吃点啥/吃什么”且没有明确在家做饭时，优先调用 `search_restaurants` 或配合 `food_decision` 给出一个可执行推荐；不要回答“没有美食推荐功能”。

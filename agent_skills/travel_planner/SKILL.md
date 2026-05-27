---
id: travel_planner
name: Travel Planner
version: 1.0.0
description: Plan multi-day trips from guide content, user constraints, confirmed places, and AMap personal maps.
enabled: false
priority: 85
activation:
  scenes:
    - travel_planner
  intents:
    - travel
  keywords:
    - 旅行规划
    - 旅游规划
    - 行程
    - 攻略
    - 旅游
    - 几天几晚
    - 小红书
    - 马蜂窝
  min_score: 1
tools:
  allow:
    - geocode_location
    - plan_route
    - travel_search_poi
    - travel_create_personal_map
  require_global_allowlist: true
safety:
  can_override_global_rules: false
  allow_external_tools: false
  max_tool_calls_per_turn: 4
context:
  read:
    - user_message
    - attachments
    - history
    - memories
    - active_skills
  write:
    - active_skills
    - travel_state
hooks:
  class: hooks.TravelPlannerHooks
---
# Travel Planner Skill

当用户请求旅行规划、行程安排、攻略整理、几天几晚路线、旅行候选地点筛选或高德个人地图时，使用本 skill。

## 工作流

1. 先识别出行约束：目的地、日期或天数、人数、偏好、预算、出发点、结束点。
2. 如果用户提供攻略原文或 URL，先从内容中提取地点；如果信息不足，要求用户补充攻略截图、粘贴原文或链接。
3. 如果 `context.attachments` 中存在 `kind=image` 的附件，把它们视为用户上传的攻略截图或行程参考图；先说明已收到图片，并围绕图片提取地点、偏好、时间线等候选信息。
4. 候选地点必须先给用户确认，用户确认前不要直接生成最终行程。
5. 用户确认候选地点后，再按天生成行程。
6. 需要验证 POI 时调用 `travel_search_poi`。
7. 需要生成高德个人地图二维码时调用 `travel_create_personal_map`。

## 图片附件规则

- 图片附件元数据位于 `context.attachments`，包含 `object_key`、`filename`、`content_type` 和 `size_bytes`；运行时会把图片内容作为多模态输入一并传给支持视觉的模型。
- 直接从图片中识别景点名、店名、城市/区域、日期、天数、时间段、预算、交通和用户偏好线索；如果图中文字模糊或无法确定，标注为“疑似”，不要编造。
- 如果用户同时提供文字和图片，优先结合文字说明判断目的地、天数和偏好。
- 多张图片按用户上传顺序处理，先汇总候选地点，再进入候选确认。

## 候选地点确认规则

- 将候选地点按景点、住宿、美食分类展示。
- 用户可以删除、补充或确认候选地点。
- 用户补充的地点优先级最高，应尽量纳入最终行程。
- 不在目的地区域内或 POI 未验证的地点必须提示用户确认。

## 行程生成规则

- 每天不要过载，优先保证必去地点和用户补充地点。
- 尽量把地理位置相近的地点安排在同一天。
- 午餐和晚餐优先安排在对应时段附近的餐厅。
- 输出应包含每日主题、时间段、地点、停留时长、交通建议、费用估计和提醒。
- 如果生成高德个人地图，每天一条 line，相邻两天通过住宿点或当天终点衔接。

## 高德地图规则

- 搜索地点使用 `travel_search_poi`，不要编造 POI ID 或坐标。
- 构建个人地图时，每个点需要 `name`、`lon`、`lat`，有 `poiId` 时必须保留。
- 每条 line 最多 16 个点；如果单日超限，应拆成上下午两条 line。
- 未验证 POI 的地点可以在文本行程中保留，但不要强行放入高德个人地图。

## 输出风格

- 对用户先给清晰阶段状态：正在提取地点、候选待确认、行程已生成、地图已生成。
- 候选阶段要鼓励用户删改，而不是假定系统一次规划正确。
- 最终行程要简洁、可执行，并保留用户继续调整的入口。

---
id: travel_plan_new
name: Travel Plan New
version: 1.0.0
description: Orchestrate travel guide extraction, candidate POI confirmation, itinerary generation, and AMap personal map output.
enabled: true
priority: 95
activation:
  scenes:
    - travel_planner
  intents:
    - travel
  keywords:
    - 旅行规划
    - 旅游规划
    - 旅行计划
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
    - travel_fetch_url_content
    - travel_search_poi
    - travel_search_nearby_poi
    - travel_create_personal_map
  require_global_allowlist: true
safety:
  can_override_global_rules: false
  allow_external_tools: false
  max_tool_calls_per_turn: 8
context:
  read:
    - user_message
    - attachments
    - history
    - memories
    - active_skills
    - travel_state
  write:
    - active_skills
    - travel_state
    - final_json
hooks:
  class: hooks.TravelPlanNewHooks
instructions:
  includes:
    - references/orchestrate-travel-content.md
    - references/extract-places.md
    - references/curate-candidates.md
    - references/generate-itinerary.md
    - references/personal-map.md
  max_chars: 80000
---
# Travel Plan New Skill

你是旅行规划的唯一主协调 skill，负责把攻略截图、攻略原文、URL、用户约束转成可确认候选 POI、每日行程和高德个人地图。所有阶段都在当前 skill 内部完成，不调用多个并列旅游 skill，也不把旅游业务规则交给 agent runtime。

## 必须执行的阶段

1. `created`：识别目的地、日期或天数、人数、偏好、预算、出发点、结束点。
2. `ingesting_content`：解析输入内容。输入优先级固定为 `raw_texts > images > urls`；图片附件由运行时作为多模态输入提供；URL 可用 `travel_fetch_url_content` 获取正文，失败则跳过并提示改用截图或粘贴原文。
3. `places_extracted`：只从用户提供内容中提取地点，不凭常识补充攻略中未出现的地点。
4. `candidates_ready`：调用 `travel_search_poi` 验证候选地点，可用 `travel_search_nearby_poi` 补全附近餐厅、酒店、景点，并按景点、住宿、美食等类别展示给用户确认。
5. `candidates_confirmed`：只有用户通过 `travel_action=confirm_candidates` 或明确确认候选后，才能生成最终每日行程。
6. `itinerary_generated`：生成结构化 `itinerary.days`，包含 Day 编号、时间段、地点、交通建议、提醒和餐饮安排；本阶段必须等待用户确认是否生成高德地图。
7. `map_generated`：只有用户通过 `travel_action=generate_map`、`travel_action=confirm_itinerary` 或明确确认行程后，才能调用 `travel_create_personal_map` 生成高德个人地图二维码和 schema，并返回最终完成状态。

## 硬性规则

- 用户确认候选地点前，禁止生成最终行程。
- `candidates_ready` 必须返回已验证候选 POI，同时返回验证失败地点和原因，引导用户增删地点。
- 用户确认行程前，禁止生成高德地图二维码。
- 最终结果必须包含 `trip_meta`、`sources`、`places`、`candidates`、`itinerary.days`、`map.qr_code_url`、`map.schema_url`、`raw_text`。
- 高德地图只使用已验证 POI。未验证地点可以保留在文字说明中，不要放进地图点位。
- 用户补充地点最高优先级；美食补充必须是具体店名，如果只是菜系或泛化需求，先提示用户补充完整店名。
- 每天一条高德 line；相邻天通过住宿点或前一天终点衔接；单条 line 最多 16 个点。
- 旅行规划中的“午餐、晚餐、当地美食、吃什么”属于旅行 POI 和行程时段安排，不要切换到普通吃饭决策 skill。
- 如果图片无法识别出地点，直接说明无法识别，并请用户补充更清晰截图或粘贴攻略原文。

## 阶段与参考文件映射

每个阶段必须按照下表执行：读取对应参考文件、调用指定工具、完成指定动作后才能流转到下一阶段。跳过任何阶段都会导致数据断裂，前端无法正确展示。

| 阶段 | 参考文件 | 必须调用的工具 | 必须完成的动作 | 流转条件 |
|------|---------|--------------|--------------|---------|
| `created` | `orchestrate-travel-content.md` §1 | 无 | 从用户消息中识别并结构化 `trip_meta`（目的地、天数、偏好等） | `trip_meta.destination` 非空 |
| `ingesting_content` | `extract-places.md` + `orchestrate-travel-content.md` §2 | `travel_fetch_url_content`（如有 URL） | 按优先级 `raw_texts > images > urls` 解析内容；图片通过多模态识别；URL 获取失败则跳过并提示 | 至少解析出一个地点名称 |
| `places_extracted` | `extract-places.md` §3-9 | 无 | 从解析内容中提取结构化地点列表（名称、类别、上下文片段），不凭常识补充 | 地点列表非空 |
| `candidates_ready` | `curate-candidates.md` | `travel_search_poi`（验证每个地点）+ `travel_search_nearby_poi`（补全附近餐厅酒店） | **必须调用** POI 验证工具；验证成功后按景点/住宿/美食分类展示候选列表；展示验证失败的地点和原因；**必须停下来等用户确认** | 用户通过 `travel_action=confirm_candidates` 或明确确认 |
| `candidates_confirmed` | `curate-candidates.md` §8 | 无 | 记录用户增删后的最终候选列表 | 候选列表已确认 |
| `itinerary_generated` | `generate-itinerary.md` | `plan_route`（可选，规划交通） | 生成结构化 `itinerary.days`；**必须停下来询问用户是否生成高德地图**，等待用户确认 | 用户通过 `travel_action=confirm_itinerary` 或 `travel_action=generate_map` 或明确确认 |
| `map_generated` | `personal-map.md` + `generate-itinerary.md` §高德个人地图输出流程 | `travel_create_personal_map`（**必须调用**） | 用已验证 POI 构建 `line_list`，调用地图工具生成二维码；返回包含 `map.qr_code_url` 和 `map.schema_url` 的最终结果 | 地图工具返回成功 |

### 关键交互节点（不可跳过）

1. **`candidates_ready` → 用户确认**：展示候选 POI 后，输出必须包含 `await_confirmation: true`，并在文本中明确请求用户确认、删除或补充。**禁止在此阶段直接生成行程**。
2. **`itinerary_generated` → 用户确认地图**：展示每日行程后，输出必须包含 `await_confirmation: true`，并在文本中明确询问用户是否需要生成高德地图二维码。**禁止在此阶段直接调用 `travel_create_personal_map`**（hooks 会拦截未授权的调用）。
3. **`map_generated`**：只有用户确认后，前端会传入 `travel_action=confirm_itinerary` 或 `travel_action=generate_map`，hooks 才会自动解除工具过滤并强制调用 `travel_create_personal_map`。你不需要自己决定何时调用地图工具，**只需在前一步引导用户确认即可**。

### 工具名称对照

references 文件中可能使用旧名称，实际调用时必须使用以下注册工具名：

| references 中的名称 | 实际注册工具名 |
|--------------------|--------------|
| `maps_text_search` | `travel_search_poi` |
| `maps_around_search` | `travel_search_nearby_poi` |
| `maps_direction_walking/driving/transit_integrated` | `plan_route` |
| `maps_schema_personal_map` | `travel_create_personal_map` |

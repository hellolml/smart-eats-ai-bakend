# Adapted Travel Reference

> 来源：travel-content-to-itinerary/SKILL.md。已适配为 `travel_plan_new` 单一 skill 内部阶段规则；不再表示多个并列 skill。高德能力统一使用后端已注册旅游工具。

# 旅行攻略转行程规划

主协调 skill，接收用户输入的攻略内容（优先支持攻略截图、粘贴原文，也支持 URL 链接）和出行约束，协调当前 skill 的内部阶段 提取地点、筛选候选、生成结构化行程并输出高德个人地图。

## 输入

1. **images** - 可选（推荐）。用户上传的攻略截图列表（图片文件路径或图片 URL）。适用于小红书截图、攻略长图等场景，LLM 多模态能力直接识别，无需网络请求，成功率最高
2. **raw_texts** - 可选。用户直接粘贴的攻略原文列表，无需网络请求即可直接解析
3. **urls** - 可选。一个或多个旅行攻略链接（博客、小红书笔记、马蜂窝攻略等），需通过网络获取内容
4. **destination** - 目的地城市或区域
5. **start_date** - 出行开始日期（YYYY-MM-DD）
6. **end_date** - 出行结束日期（YYYY-MM-DD）
7. **preferences** - 可选。旅行风格偏好（如：美食、文化、自然、休闲慢节奏）
8. **travelers_count** - 出行人数
9. **budget** - 可选。预算级别（经济 / 适中 / 奢华）
10. **special_requirements** - 可选。无障碍需求、饮食限制、必去地点等
11. **start_point** - 可选。行程出发地点（如"西宁站"、"兰州中川机场"），必须在目的区域内。提供后将作为 Day 1 行程的第一个点，辅助路线规划
12. **end_point** - 可选。行程结束地点（如"西宁站"、"兰州中川机场"），必须在目的区域内。提供后将作为最后一天行程的最后一个点，辅助路线规划
13. **output_platforms** - 可选，支持多选。输出平台列表，默认 ["json", "amap_personal_map"]。可选值：
    - `json` - 结构化 JSON 数据（始终返回）
    - `amap_personal_map` - 高德个人地图二维码（默认返回，用高德地图 App 扫码打开）
    - `pdf` - 可选，可打印的逐日行程指南

> **输入优先级**: `raw_texts` > `images` > `urls`。前两者不依赖网络请求，成功率更高，推荐优先使用。三种输入至少提供一种。

## 输出

结构化行程 JSON 对象，schema 如下：

```json
{
  "session_id": "string",
  "state": "itinerary_generated",
  "trip_meta": {
    "destination": "string",
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "travelers_count": 2,
    "preferences": ["string"],
    "budget": "moderate"
  },
  "itinerary": {
    "days": [
      {
        "date": "YYYY-MM-DD",
        "theme": "string",
        "items": [
          {
            "time_slot": "09:00-11:00",
            "place_name": "string",
            "place_type": "attraction|restaurant|hotel|transport|shopping",
            "poi_id": "amap_poi_id",
            "duration_minutes": 120,
            "notes": "string",
            "transport_to_next": { "mode": "walk|taxi|metro|bus", "duration_minutes": 15 }
          }
        ]
      }
    ]
  },
  "summary": "string",
  "tips": ["string"]
}
```

## 流程

### 1. 会话初始化（状态：created）

- 生成唯一的 `session_id` 用于追踪。
- 解析并校验用户输入，转为 `trip_meta` 结构化数据。
- 如有歧义（如日期格式、目的地不明确），向用户确认。

### 2. 内容解析（状态：ingesting_content）

- 将 `urls`、`images`、`raw_texts`（如有）和 `trip_meta` 传递给当前 skill 的地点提取阶段。
- 当前 skill 的内部阶段 按优先级处理输入：`raw_texts` > `images` > `urls`。前两者不需要网络请求，可直接解析；`urls` 通过普通 HTTP 请求爬取内容（失败则跳过并告警）。
- 如所有输入均无法获取有效内容，向用户请求替代输入方式（如改用截图或粘贴原文）。
- 等待结构化地点列表返回。
- 成功后流转到 `places_extracted`。

### 3. 候选地点筛选（状态：places_extracted → candidates_ready）

- 将提取的地点 + `trip_meta` 传递给当前 skill 的候选筛选阶段。
- 接收已评分、去重、分类、区域校验后的候选列表。

### 4. 用户确认（状态：candidates_ready → candidates_confirmed）

> **此步骤不可跳过。** 所有候选地点必须经过用户确认后才能进入行程规划。

由 `curate-trip-candidates` 的步骤 8 驱动，具体流程：

1. **分类展示**：按景点、住宿、美食三大类分组展示候选地点（含评分、来源、推荐理由、区域校验备注）。
2. **用户操作**：
   - **剔除**：用户可指定不想要的地点，从候选中移除。
   - **补充**：用户可补充景点、住宿、美食。美食必须是具体店名（高德可查 POI），否则提示用户补充完整店名。
   - **区域提醒**：不在目的区域内的地点会有温馨提示，用户确认保留后纳入行程。
3. **循环确认**：每次删除/补充后重新展示更新后的候选列表，直到用户明确确认（如"没问题"、"可以了"、"确认"）。
4. 用户确认后，流转到 `candidates_confirmed`。

### 5. 行程生成（状态：candidates_confirmed → itinerary_generated）

- 将确认的候选地点 + `trip_meta` + `output_platforms` 传递给当前 skill 的行程生成阶段。
- 接收结构化行程。
- 如 `output_platforms` 包含 `amap_personal_map`，同时返回高德个人地图二维码。
- 向用户展示最终行程供审阅。

### 6. 修订循环（状态：itinerary_generated ↔ revised）

- 如用户要求修改，将修订指令传回 `generate-itinerary-from-candidates`。
- 循环直到用户满意。
- 按用户要求的格式输出最终行程。

### 状态机总览

```
created → ingesting_content → places_extracted → candidates_ready → candidates_confirmed → itinerary_generated ↔ revised
                                                      ↑                    |
                                                      |  (用户删除/补充)    |
                                                      └────────────────────┘
```

> `candidates_ready` 状态下，用户可反复删除和补充候选地点（循环回到 `candidates_ready`），直到明确确认后才流转到 `candidates_confirmed`。

### 错误处理

**输入校验错误：**
- URL 无法访问 → 跳过并告知用户哪些链接失败，建议改用截图或粘贴原文。
- 目的地不存在或拼写不明确 → 提示用户检查拼写或提供更具体的区域名称。
- 日期格式错误或逻辑错误（end_date < start_date）→ 提示正确格式（YYYY-MM-DD）并要求修正。

**当前 skill 的内部阶段 执行错误：**
- 未提取到任何地点 → 请求用户提供替代 URL、粘贴原文或上传截图。
- 当前 skill 的内部阶段 kill 超时 → 重试一次（最多 2 次），仍失败则报告部分结果并询问是否继续。
- 候选地点不足（如只有 1~2 个地点却要规划 3 天行程）→ 提示用户补充更多地点或减少行程天数。

**高德 API 错误：**
- POI 搜索无结果 → 标记该地点为"未验证"，在行程中标注"需人工确认位置"。
- API 调用失败（网络异常、配额耗尽等）→ 回退到距离估算，并在行程中标注"交通时间仅供参考"。

## 示例

**示例 1（截图输入 — 推荐）：** "帮我规划成都3天行程，这是我收藏的攻略截图，两个人出行，喜欢美食和逛街，休闲游" + [用户上传的攻略截图]

**执行步骤：**
1. 解析输入 → destination: 成都, travelers: 2, preferences: [美食, 逛街], style: 休闲
2. 获取用户提供的图片文件路径，执行 `extract-places-from-travel-urls`，传入 images 列表，LLM 多模态识别图片中的地点信息
3. 执行 `curate-trip-candidates`，传入提取的地点 + 偏好
4. 展示候选地点 → 用户确认
5. 执行 `generate-itinerary-from-candidates` → 输出 3 天行程 + 高德个人地图二维码

**示例 2（粘贴攻略原文）：** "帮我规划一个杭州3天2晚的行程，两个人出行，喜欢美食和自然风光。攻略内容如下：（粘贴攻略原文）"

**执行步骤：**
1. 解析输入 → destination: 杭州, travelers: 2, preferences: [美食, 自然风光]
2. 执行 `extract-places-from-travel-urls`，传入 raw_texts，直接解析无需网络请求
3. 后续流程同示例 1

**示例 3（URL 输入）：** "帮我规划一个杭州3天2晚的行程，我有这几个攻略：[url1] [url2]，两个人出行"

**执行步骤：**
1. 解析输入 → destination: 杭州, travelers: 2
2. 执行 `extract-places-from-travel-urls`，传入 urls: [url1, url2]
3. 后续流程同示例 1

> **输入方式推荐优先级**：截图/图片 > 粘贴攻略原文 > URL 链接。前两者不依赖网络请求，成功率更高。

**结果：** 结构化的多天行程，包含上午/下午/晚上时段安排、餐厅推荐、交通建议、高德个人地图二维码。

## 故障排查

**URL 无法获取有效内容。**
原因：链接需要登录或页面结构已变更。
解决方案：请用户上传攻略截图或直接粘贴文章内容。

**状态卡在 ingesting_content。**
原因：网络问题或不支持的 URL 格式。
解决方案：跳过有问题的 URL，继续处理已成功解析的链接。建议用户改用截图方式输入。

**用户想回到之前的状态。**
原因：用户改变了对候选地点或约束条件的想法。
解决方案：允许回滚到任意之前的状态。用更新后的输入重新运行下游当前 skill 的内部阶段。

## 高德 API 调用规范

高德能力统一使用后端已注册旅游工具：`travel_search_poi`、`travel_search_nearby_poi`、`plan_route`、`travel_create_personal_map`。不要复制或引用外部 personal-map 脚本，不要在 prompt 中暴露 API Key 示例。详细地图和 lineList 规则见 `personal-map.md`。

## 参考

- 本 skill 候选筛选阶段：详见 `curate-candidates.md`
- 本 skill 行程生成阶段：详见 `generate-itinerary.md`
- 高德工具能力：`travel_search_poi`（POI 搜索验证）、`travel_search_nearby_poi`（周边搜索）、`plan_route`（路径规划）、`travel_create_personal_map`（个人地图二维码生成）。**所有高德 API 调用必须通过以上注册工具，禁止直接调用高德 REST API**

# Adapted Travel Reference

> 来源：generate-itinerary-from-cadidates/SKILL.md。已适配为 `travel_plan_new` 单一 skill 内部阶段规则；不再表示多个并列 skill。高德能力统一使用后端已注册旅游工具。

# 根据候选地点生成行程

将用户确认的候选地点组装为优化的逐日行程，综合考虑地理邻近性、营业时间、用餐时段和用户偏好。

## 输入

1. **candidates** — 来自 `curate-trip-candidates` 输出的已确认候选列表（经用户审核）。其中 `source: "user_added"` 的地点为用户手动补充，享有最高优先级，**必须全部纳入行程**
2. **trip_meta** — 出行元数据（目的地、开始日期、结束日期、人数、偏好、预算）
3. **start_point** — 可选。行程出发点（如"西宁站"、"兰州中川机场"），必须在目的区域内。提供后将作为 Day 1 行程的第一个点
4. **end_point** — 可选。行程结束地点（如"西宁站"、"兰州中川机场"），必须在目的区域内。提供后将作为最后一天行程的最后一个点
5. **revision_instructions** — 可选。用户对行程调整的反馈（用于 revised 状态）
6. **output_platforms** — 可选。支持多选。输出平台列表，默认 ["json", "amap_personal_map"]。可选值：
   - `json` — 结构化 JSON 数据（始终返回）
   - `amap_personal_map` — 高德个人地图二维码（默认返回，扫码在高德地图 App 中打开）
   - `pdf` — 可选，可打印的逐日行程指南

## 输出

结构化行程 JSON 对象：

```json
{
  "session_id": "string",
  "version": 1,
  "trip_meta": { "destination": "string", "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD" },
  "days": [
    {
      "day_number": 1,
      "date": "YYYY-MM-DD",
      "theme": "string",
      "items": [
        {
          "order": 1,
          "time_slot": "09:00-11:00",
          "place_name": "string",
          "place_type": "attraction|restaurant|hotel|transport|shopping|nature|cafe",
          "candidate_id": "candidate_001",
          "amap_poi_keyword": "string",
          "duration_minutes": 120,
          "notes": "string",
          "cost_estimate_yuan": 0,
          "transport_to_next": {
            "mode": "walk|taxi|metro|bus|drive",
            "duration_minutes": 15,
            "distance_km": 2.5,
            "notes": "string"
          }
        }
      ],
      "day_summary": "string",
      "total_cost_estimate_yuan": 0
    }
  ],
  "total_cost_estimate_yuan": 0,
  "packing_suggestions": ["string"],
  "booking_reminders": ["string"],
  "tips": ["string"]
}
```

## 流程

### 1. 约束分析

- 计算行程天数。
- 确定每日可用时间预算（通常 09:00–21:00，约 12 小时）。
- 分配用餐时段：早餐（07:30-09:00）、午餐（11:30-13:00）、晚餐（18:00-19:30）。
- 为休闲节奏的旅客预留缓冲时间（每个项目之间加 30 分钟缓冲）。
- 考虑酒店入住（第一天下午）和退房（最后一天上午）。

### 2. 地理聚类

- 使用 `nearby_candidates` 数据按地理邻近性分组。
- 将聚类分配到不同天，最小化跨区域移动。
- 优先将相邻地点安排在同一天。

### 3. 时段分配

每天的安排策略：

- 上午（09:00~12:00）： 户外景点、自然景观（光线好、人少）。
- 午餐（12:00~13:30）： 附近候选餐厅。
- 下午（13:30~17:30）： 博物馆、寺庙、购物（炎热/雨天的室内选项）。
- 晚餐（18:00~19:30）： 符合偏好的餐厅。
- 晚上（19:30~21:00）： 可选的夜生活、夜景、休闲散步。

尊重候选数据中的 `recommended_time_slots` 和 `best_visit_time`。

**营业时间约束**：
- 安排地点时，必须检查候选数据中的 `business_hours` 字段，确保规划的**到达时间和离开时间**都落在营业时间范围内。离开时间 = 到达时间 + `duration_minutes`
- `business_hours` 格式为 `"HH:MM–HH:MM"`（24小时制）。若跨天营业（如 `"22:00–02:00"`），需特殊处理：规划时间在 22:00-23:59 或 00:00-02:00 范围内均视为在营业时间内
- 若 `business_hours` 为 `null`（未获取到），视为全天可访问，不做限制
- 若某地点营业时间较短（如仅上午开放），优先将其安排在营业窗口内，再围绕它安排其他灵活地点
- 餐厅类地点需确保用餐时段与其营业时间重合（如午餐时段安排的餐厅需在 11:30-13:30 营业，且到达时间 + 用餐时长 ≤ 餐厅停止接单时间）

### 4. 路线优化（调用高德路径规划）

> 所有高德 API 调用必须通过 后端已注册旅游工具 统一进行，禁止绕过后端工具直接调用高德 REST API。详见本 skill 的 `personal-map.md`。

- 在每一天内，按最小化相邻地点间出行时间排序。
- 使用候选地点中的精确坐标（`longitude`, `latitude`），通过 `后端高德工具封装` 调用高德路径规划获取真实交通数据：
  - 距离 < 1km → 执行 `client.maps_direction_walking(origin, destination)` 获取步行时间
  - 距离 1-5km → 执行 `client.maps_direction_transit_integrated(origin, destination, city)` 获取公交/地铁时间
  - 距离 > 5km → 执行 `client.maps_direction_driving(origin, destination)` 获取驾车时间
- 用 API 返回的真实时长和距离替换估算值，填入 `transport_to_next`
- 若 API 调用失败，回退到距离估算（加 30% 缓冲）

### 5. 平衡与校验

- **用户补充地点优先保障**：`source: "user_added"` 的地点必须全部出现在行程中，不可因任何优化原因被移除或降级。若天数不足，优先挤掉低分的攻略提取地点（`source: "extracted"`），为用户补充点腾出空间
- 确保每天不过载（不含餐饮最多 5-6 个项目）
- 验证每天总时长不超过可用时间
- 检查必去地点（分数 >= 8 或 `source: "user_added"`）均已纳入
- 平衡活动强度（尽量交替安排紧凑和轻松的日程）
- 确保每天至少安排 2 顿正餐
- 若用户补充地点与已有行程地理位置冲突（距离过远），优先调整周围的攻略提取地点，保留用户补充地点不动
- **营业时间冲突检测**： 逐一检查每个已安排地点的规划**到达时间和离开时间**（到达时间 + `duration_minutes`）是否都在其 `business_hours` 范围内。若发现冲突：
  - 优先尝试在当天内调换顺序，将该地点移至营业时间窗口内，同时确保调整后其他地点的时间仍然合理
  - 若当天无法调整，尝试将该地点移至其他天的合适时段
  - 若仍无法解决，在该地点的 `notes` 中标注 `⚠️ 到达/离开时间可能不在营业时间内（营业时间：xx:xx–xx:xx，计划到达：xx:xx，计划离开：xx:xx），建议提前确认或调整行程`。

### 6. 补充实用信息

- 根据活动添加 `packing_suggestions`（如：徒步需要运动鞋）。
- 为需要预约的地点添加 `booking_reminders`。
- 添加每日的 `tips`（天气、人流、后勤提醒）。
- 计算每个项目的 `cost_estimate_yuan` 和每日合计。

### 7. 修订处理（状态：`revised`）

当提供 `revision_instructions` 时：
- 解析用户反馈（交换地点、调整顺序、增删项目、调节节奏）。
- 在保持路线优化的前提下应用变更。
- 递增 `version`、版本号。
- 重新校验平衡性和时间约束。

### 8. 多平台输出

根据 `output_platforms` 列表生成对应格式：

- **json**（默认）：返回上述结构化对象。
- **pdf**：格式化为可打印的逐日行程指南（含地图占位）。
- **amap_personal_map**：调用高德 `maps_schema_personal_map` 生成个人地图二维码。

#### 高德个人地图输出流程

当 `output_platforms` 包含 `amap_personal_map` 时：

##### 核心原则：每天一条线，天与天通过共享端点衔接

高德个人地图 `lineList` 支持多条 line，每条 line 在 App 中会以**不同视觉样式**自动区分。利用这一特性，将每天的行程拆分为独立的 line，通过共享端点保证连贯性。

**🔴 强制约束（不可违反）：**

> **Day N 的最后一个点必须与 Day N+1 的第一个点完全相同（经纬度和 poiId 一致）。不允许出现任何路线断裂。**

具体规则：
- **每天一条 line**：每天的行程组成一条独立的 line，`title` 标注为 `Day X: 主题`。
- **前一天终点 = 下一天起点（强制）**：确定衔接点的优先级为：① 若当天有住宿点，使用住宿点；② 若无住宿点，使用当天行程的最后一个地点。该衔接点**必须**作为下一天 line 的第一个点重复出现（经纬度和 poiId 完全相同），确保地理连贯。
- **start_point 处理**：若提供了 `start_point`（如"西宁站"），先通过 `maps_text_search` 获取 POI，然后将其作为 Day 1 的第一个点（标记为 `D1🚅 {地点名}`）。
- **end_point 处理**：若提供了 `end_point`（如"西宁站"），先通过 `maps_text_search` 获取 POI，然后将其作为最后一天的最后一个点（标记为 `D{N}🚅 {地点名}`）。
- **视觉区分**：高德地图 App 会为不同的 line 自动分配不同的视觉样式（颜色/线型），用户打开地图即可直观看到每天行程的区分。
- **每条 line 最多 16 个点**：单日行程通常不会超过此限制；若极端情况超限，拆为上下午两条 line。
- **同一条 line 内禁止重复 `poiId`**：高德个人地图会合并相同 `poiId` 的点，导致路线显示异常（断线或不显示）。如果行程需要回到起点（闭环），应省略末尾的重复点，或替换为附近的其他 POI。注意：相邻 line 之间的共享端点不受此限制（那是跨 line 的衔接，不在同一条 line 内）。

##### 构建步骤

1. **按天拆分为多条 line，共享端点保证连贯**：

  ```json
  [
    {
      "title": "Day 1: 西宁→青海湖→茶卡",
      "pointInfoList": [
        {"name": "D1⛩️ 塔尔寺", "lon": 101.567, "lat": 36.487, "poiId": "B03CB06N7F"},
        {"name": "D1⛰ 拉脊山", "lon": 101.550, "lat": 36.321, "poiId": "B03CE0075H"},
        {"name": "D1🏞 青海湖 | 涅鱼·手抓羊肉", "lon": 100.495, "lat": 36.578, "poiId": "B03CE0000D"},
        {"name": "D1🧂 茶卡盐湖 | 灰锅羊肉", "lon": 99.078, "lat": 36.759, "poiId": "B03D1008S8"},
        {"name": "D1🏨 茶卡住宿", "lon": 99.080, "lat": 36.760, "poiId": "..."}
      ]
    },
    {
      "title": "Day 2: 茶卡→翡翠湖→大柴旦",
      "pointInfoList": [
        {"name": "D2🏨 茶卡住宿", "lon": 99.080, "lat": 36.760, "poiId": "..."},
        {"name": "D2🧂 察尔汗盐湖", "lon": 95.192, "lat": 36.949, "poiId": "B03D100I00"},
        {"name": "D2💎 翡翠湖 | 干锅牦牛肉", "lon": 95.265, "lat": 37.866, "poiId": "B0FFJZ6MJD"},
        {"name": "D2🏨 大柴旦住宿", "lon": 95.370, "lat": 37.850, "poiId": "..."}
      ]
    },
    {
      "title": "Day 3: 大柴旦→敦煌",
      "pointInfoList": [
        {"name": "D3🏨 大柴旦住宿", "lon": 95.370, "lat": 37.850, "poiId": "..."},
        {"name": "D3⛰ 南八仙雅丹", "lon": 94.233, "lat": 38.443, "poiId": "B0FFH0ERRC"},
        {"name": "D3🏛 莫高窟", "lon": 94.889, "lat": 40.042, "poiId": "B03A900102"},
        {"name": "D3⛰ 鸣沙山月牙泉", "lon": 94.680, "lat": 40.088, "poiId": "B03A99000ZN"},
        {"name": "D3🌙 沙洲夜市 | 驴肉黄面·杏皮茶", "lon": 94.666, "lat": 40.139, "poiId": "B03A913ZAS"}
      ]
    }
  ]
  ```

  > 注意：`Day 2` 的第一个点 （`D2🏨 茶卡住宿`） 与 `Day 1` 的最后一个点相同， 经纬度完全一致，保证两条 line 在地图上首尾相连。

2. **命名规则**：
- **天数前缀**：`D1`、`D2`、`D3` ... 紧贴在名称最前面，用于区分每天行程
- **类目 emoji**：紧跟天数后面，用于区分地点类型
- **美食信息**：若该地点有攻略提及的特色美食（来自 `associated_food`），用全角 `｜` 分隔附在地点名后面，多个美食用 `·` 连接。不需要单独查询美食经纬度，直接展示在地点名中
- **emoji 类目映射**：
  - 🌿 自然景观 (nature)
  - 🏛 文化景点 (attraction/museum)
  - 🏯 寺庙宗教 (temple)
  - 🌳 公园 (park)
  - 🍜 餐厅美食 (restaurant)
  - ☕ 咖啡茶饮 (cafe)
  - 🏨 住宿 (hotel)
  - 🛍 购物 (shopping)
  - 🌙 夜生活 (nightlife)
  - 🚉 交通枢纽 (transport_hub)
- **衔接点规则**：确定下一天起点的优先级为：① 若当天有住宿点 (`D{N}🏨 {城市}住宿`)，使用住宿点作为衔接；② 若无住宿点，直接使用当天行程的最后一个地点作为衔接。衔接点同时作为下一天 line 的第一个点重复出现。第一天可省略起点，最后一天可省略末尾。
  - **无美食例**：`D1🏯 塔尔寺`
  - **有美食例**：`D1🏞 青海湖 | 涅鱼·手抓羊肉`
  - **夜市类**：`D4🌙 沙洲夜市 | 驴肉黄面·敦煌酿皮`
  - **住宿衔接**：`D2🏨 大柴旦住宿`

3. **line 的 title 命名**：`Day {N}: {起点城市}→{终点城市}` 或 `Day {N}: {当日主题}`。

4. **选择 sceneType**：使用 `sceneType=1`（创建资源源点 + 路线），行程既有打卡点又有路线关系。

5. **调用生成**：`maps_schema_personal_map(orgName="行程名称", lineList=lineList, sceneType=1)`。

6. **返回结果**：将 `qr_code_url` 包含在输出中，用户扫码即可在高德地图 App 中打开完整行程。

**注意事项：**

- 仅使用 `poi_verified: true` 且有 `amap_poi_id` 的地点构建 `lineList`。
- 住宿点如无精确 POI，可使用城市中心点或通过 `maps_text_search` 搜索酒店名获取。
- 未验证 POI 的地点跳过，在输出中标注"以下地点未在高德地图中标记"。
- 确保每条 line 内 pointInfoList 中点的顺序严格按照行程时间顺序排列，不出现地理上的回头路。
- 相邻两条 line 通过共享端点实现地理连贯，高德 App 会为不同 line 自动区分视觉样式。
- 若提供了 `start_point`，Day 1 的 pointInfoList 第一个点为 `start_point`（标记 `D1🚅`）。
- 若提供了 `end_point`，最后一天的 pointInfoList 最后一个点为 `end_point`（标记 `D{N}🚅`）。

**🔴 连贯性校验（生成后必须执行）：**

生成 lineList 后，**必须**逐条检查相邻 line 的衔接点，确认满足以下条件：

```
for i in range(len(lineList) - 1):
    day_end = lineList[i]['pointInfoList'][-1]       # 第 i 天最后一个点
    next_start = lineList[i+1]['pointInfoList'][0]   # 第 i+1 天第一个点
    assert day_end['lon'] == next_start['lon']       # 经度一致
    assert day_end['lat'] == next_start['lat']       # 纬度一致
    assert day_end['poiId'] == next_start['poiId']   # POI ID 一致
```

若校验发现任何不一致，**必须修复后再执行 `maps_schema_personal_map`**，不允许带着断裂的路线生成地图。

## 示例

**输入：** 杭州 3 天行程的 10 个已确认候选地点，preferences: [美食, 自然], 2 人出行，适中预算

**执行步骤：**

1. 聚类：西湖周边（西湖、花港观鱼、知味观湖滨店），灵隐区（灵隐寺、龙井村），城区（河坊街、南宋御街）
2. 第 1 天：西湖日游 → 花港观鱼 → 知味观午餐 → 断桥残雪 → 白堤漫步 → 晚餐
3. 第 2 天：灵隐寺 → 龙井村品茶 → 午餐 → 九溪烟树徒步 → 晚餐
4. 第 3 天：河坊街 → 南宋御街 → 午餐 → 返程

**结果：** 3 天行程，每天 5-6 个项目，节奏均衡，预估总费用 2000 元。

**修订示例：**
用户说："第二天太累了，把九溪烟树移到第三天"
→ 将九溪烟树移到第 3 天上午，重新优化第 3 天路线，递增版本号。

## 边界情况处理

- **单点行程**：若某天只有一个地点（如"全天在酒店休息"），lineList 中仍生成包含该点的 line，高德地图会显示为一个标记点（无路线）。
- **两点行程**：若某天只有两个地点，生成包含两点的 line，高德地图正常显示为一条连线。
- **空行程**：若某天没有安排任何地点（如"自由活动时间"），跳过该天不生成对应的 line，但在输出 JSON 的 `days` 数组中保留该天的占位（`items: []`），`day_summary` 标注"自由活动"。
- **跨区域大跨度**：若一天的行程跨度距离 > 200km，在 `tips` 中提醒用户"今日行程跨度较大，建议预留充足交通时间"。
- **start_point / end_point 搜索失败**：若通过 `maps_text_search` 无法找到对应 POI，在 `warnings` 中标注"起点/终点未找到对应 POI，已忽略"，并按无起终点的方式生成行程。

## 故障排查

**候选地点太多，天数不够。**
原因：用户确认的地点数超过了可容纳数量。
解决方案：按评分优先排列。建议去掉低分候选或延长行程。展示备选方案。

**地理聚类导致天数不均衡。**
原因：大部分候选集中在一区域。
解决方案：将同区域的游览分散到多天，每天安排不同主题（上午自然、下午文化）。

**交通时间估算不合理。**
原因：直线距离无法反映实际路线。
解决方案：在预估交通时间基础上增加 30% 缓冲。标注实际时间受路况影响。

**用户要求高德路线但地点无法匹配 POI。**
原因：地点名称过于笼统或是新开场所。
解决方案：使用 `amap_poi_keyword` 搜索。若仍无法找到，提供地址并建议手动标注。

## 参考

- 父 skill：`travel-content-to-itinerary` — 协调整个规划流程
- 流水线上游：当前 skill 的候选筛选阶段 — 提供已评分的候选列表（含精确坐标和 POI ID）
- 外部依赖：后端高德工具封装（高德个人地图）— 提供路径规划 API 和个人地图二维码生成能力
  - `maps_direction_walking/driving/transit_integrated` — 真实路径规划
  - `maps_schema_personal_map` — 生成个人地图小程序二维码

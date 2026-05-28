# 高德个人地图与路线输出规范

> 来源：personal-map-1.0.0/SKILL.md。此文件只保留接口规范、异常策略和 lineList 规则；实现上统一使用后端工具 `travel_search_poi`、`travel_search_nearby_poi`、`plan_route`、`travel_create_personal_map`，不复制外部脚本或 API key 配置。

## 能力边界

- `travel_search_poi`：关键词验证 POI，获取 `poi_id`、名称、地址、经纬度。
- `travel_search_nearby_poi`：基于中心点周边搜索餐厅、景点、酒店、交通点等候选。
- `plan_route`：基于坐标规划步行、骑行、公交或驾车路线。
- `travel_create_personal_map`：使用高德个人地图 schema 生成二维码和 schema URL。

## 何时生成地图

- 只有用户确认行程后，才可以调用 `travel_create_personal_map`。
- 候选地点确认前禁止生成最终行程和地图。
- 行程生成后需要停在确认阶段，询问用户是否生成高德地图。
- 用户通过 `travel_action=confirm_itinerary`、`travel_action=generate_map` 或明确说“生成地图/生成二维码/可以了”后，才能生成地图。

## POI 使用规则

- 地图点位只允许使用已验证 POI。
- 已验证 POI 必须包含 `poi_id`、`name`、`longitude`、`latitude`。
- 未验证地点可以保留在文字行程和 warnings 中，但不得进入 `lineList.pointInfoList`。
- 用户补充地点优先级最高；即使暂未验证，也要保留在候选列表并提示需要准确 POI。
- 美食补充必须是具体店名，不能只写“火锅”“小吃街附近吃饭”这类泛化需求。

## lineList 结构

`travel_create_personal_map` 的 `line_list` 使用高德个人地图结构：

```json
[
  {
    "title": "Day 1: 西湖经典线",
    "pointInfoList": [
      {"name": "西湖", "poiId": "B001", "lon": 120.148, "lat": 30.242}
    ]
  }
]
```

## lineList 构造规则

- 每天一条 line，`title` 使用 `Day {N}: {当日主题}`。
- 每条 line 最多 16 个点；超限时拆成上下午两条 line。
- 同一条 line 内禁止重复 `poiId`，避免高德合并点位导致路线异常。
- 点位顺序必须与行程时间顺序一致。
- 相邻天通过共享端点衔接：Day N 最后一个点应与 Day N+1 第一个点一致。
- 衔接点优先级：当天住宿点 > 当天最后一个地点。
- 如果用户提供 `start_point`，先用 POI 搜索验证，作为 Day 1 第一个点。
- 如果用户提供 `end_point`，先用 POI 搜索验证，作为最后一天最后一个点。
- 若起终点搜索失败，在 warnings 中说明并按无起终点生成。

## sceneType

- 旅行行程默认使用 `scene_type=1`：创建资源点并创建路线。
- 纯地点收藏可使用 `scene_type=2`。
- 纯路线规划可使用 `scene_type=3`。

## 生成前校验

生成地图前必须校验：

- `line_list` 非空。
- 每条 line 至少 1 个有效点。
- 每个点有 `name`、`lon`、`lat`，优先包含 `poiId`。
- 未验证 POI 不进入地图。
- 相邻 line 的共享端点经纬度和 `poiId` 一致；不一致时先修复再调用地图工具。

## 异常处理

- 高德 POI 无结果：候选保留为未验证，提示用户补充准确名称或地址。
- 周边搜索无结果：不阻断主流程，只减少周边推荐。
- 路线规划失败：回退为距离/方向性说明，并标注交通时间仅供参考。
- 地图生成失败：返回 itinerary 和候选列表，提示稍后重试生成地图。

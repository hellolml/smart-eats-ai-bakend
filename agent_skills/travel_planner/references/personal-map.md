# Personal Map Reference

高德个人地图用于将多天行程展示为可扫码打开的地图路线。

调用工具：

- `travel_search_poi`: 验证地点、获取 POI ID 和经纬度
- `travel_create_personal_map`: 生成个人地图二维码和 schema URL

lineList 规则：

- 每天一条 line。
- 每条 line 的 `pointInfoList` 按当日行程顺序排列。
- 相邻两天建议共享住宿点或前一天终点作为衔接。
- 单条 line 最多 16 个点。
- 点结构使用 `name`、`lon`、`lat`、`poiId`。

只将已验证坐标的地点放入地图。未验证地点可以保留在文本行程中，并提示用户人工确认。

# Generate Itinerary Reference

从用户确认后的候选地点生成逐日行程。

输入：

- confirmed candidates
- trip meta
- start point
- end point
- revision instructions
- output platforms

生成规则：

- 每天按上午、午餐、下午、晚餐、晚上组织。
- 地理位置接近的地点尽量放在同一天。
- 每天不含餐饮建议不超过 5 到 6 个项目。
- 用户补充地点必须尽量纳入。
- 检查营业时间，发现冲突时尝试调换顺序或给出提醒。

输出：

- 每日主题
- 时间段
- 地点名称和类型
- 停留时长
- 交通建议
- 费用估算
- 每日提示
- 装备、预约和天气提醒

需要生成地图时，调用 `travel_create_personal_map`。

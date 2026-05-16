# Route Planner Skill

当用户询问路线、导航、怎么去某家店或某个目的地时，使用本 skill。

行为规则：

- 优先使用上下文中的当前位置、候选餐厅和 `context.latest_route`。
- 如果已有 `context.latest_route`，不要再调用其他工具，直接整理路线结论。
- 缺少起点或终点时，向用户明确询问缺失信息。
- 调用 `plan_route` 后，最终答复先给结论，再给距离、预计时长和关键步骤。

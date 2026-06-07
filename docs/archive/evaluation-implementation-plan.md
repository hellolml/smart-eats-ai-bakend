> ⚠️ **已过期** — 本文档是早期 Phase 0-6 分阶段实施计划，所有 Phase 的目录结构和实施步骤都与当前实际代码不匹配。后续实现走了不同的路线（fixture/live 双模式、PostgreSQL 持久化、Web 工作台）。仅供参考，请以 `evaluation-usage-guide.md` 和实际代码为准。

# Smart-Eats Agent 评测体系实施计划与接入步骤

## 1. 文档目标

本文档是 Smart-Eats Agent 评测体系的**最终落地执行文档**，目标不是再解释理念，而是回答下面这些实施问题：

1. 我们到底要做哪些评测
2. 每类评测分别评什么
3. 每类评测依赖哪些数据
4. 每类评测放在 Phoenix 还是 DeepEval
5. 代码具体改哪些目录、哪些文件
6. 按什么顺序实施
7. 每个阶段做完如何验收
8. 本地怎么跑，后续 CI 怎么接

本文档应当让研发、测试、Agent 调优人员在第一次阅读时，就能明确看到：

- **做哪些测评**
- **为什么这么做**
- **如何做到**
- **落到仓库哪里**
- **先做什么、后做什么**

---

## 2. 与前序文档的关系

本文件是前三份文档的执行版：

- 总体架构： [docs/phoenix-deepeval-evaluation-architecture.md](docs/phoenix-deepeval-evaluation-architecture.md)
- 目录与模块： [docs/evaluation-directory-and-module-design.md](docs/evaluation-directory-and-module-design.md)
- 数据集与 Evaluator： [docs/evaluation-dataset-and-evaluator-design.md](docs/evaluation-dataset-and-evaluator-design.md)

如果说前三份文档分别回答的是：

- 为什么做
- 放哪做
- 评什么

那么本文档回答的是：

- **怎么把它一步步做出来**

---

## 3. 最终要建成的评测能力总览

先给出最终目标图景。

当本文档所述实施全部完成后，Smart-Eats 应该具备下面这套评测能力：

### 3.1 Phoenix 侧能力

用于“观察、诊断、实验对比”的能力：

- 能看到一次请求从 API 进入到最终回答的完整 trace
- 能看到 graph 节点、工具调用、恢复路径、最终输出
- 能统计成功率、fallback rate、步骤数、工具数、延迟、token 消耗
- 能在同一批样本上对比不同 prompt / graph / tool 策略
- 能按场景标签（eat_out / cook_home / route / clarify）切片看表现

### 3.2 DeepEval 侧能力

用于“回归守门、阻止退化”的能力：

- 能对关键样本集执行自动化回归测试
- 能检查结构、规则、业务质量底线
- 能在本地跑轻量回归集
- 能在 CI 跑核心回归集
- 能对高风险历史 bug 做长期守门

### 3.3 数据集侧能力

用于“长期沉淀评测资产”的能力：

- 有正式的 golden / regression / stress 三层样本集
- 每条样本有统一字段、标签、来源与期望
- 现有 replay case 不再只是测试夹具，而是正式 dataset seed
- 未来线上 bad case 可以持续回流进数据集

### 3.4 业务侧结果

最终达到下面这些可见效果：

- 不是只知道“模型表现不稳定”，而是知道**哪类任务不稳定**
- 不是只知道“出错了”，而是知道**错在意图、工具、恢复还是输出**
- 不是只知道“改了 prompt”，而是知道**改完哪些维度变好/变差**
- 不是只靠人肉回归，而是有**持续自动守门**

---

## 4. 我们具体要评测哪些内容

这一节是全文最关键的“总表”。

下面按你关心的业界 Agent 评测维度，明确说明 Smart-Eats 最终要做的评测项、如何做、在哪里做。

---

## 4.1 评测总表

| 评测大类 | 具体评测项 | 是否纳入首期 | 主要执行位置 | 主要判定方式 |
|---|---|---:|---|---|
| 任务达成度 | 任务成功率 | 是 | Phoenix + DeepEval | 规则型 |
| 任务达成度 | 完成质量 | 是 | Phoenix 为主 | 规则型 + LLM Judge |
| 任务达成度 | 最优路径占比 | 否，二期 | Phoenix | 轨迹型 |
| 推理与规划 | 逻辑连贯性 | 是 | Phoenix + DeepEval | 规则型 + 轨迹型 |
| 推理与规划 | 动态调整 / Re-planning | 是 | Phoenix + DeepEval | 恢复路径规则 + 轨迹型 |
| 推理与规划 | 显式任务分解 | 否，当前弱化 | Phoenix | 轨迹型 |
| 工具调用 | 工具选择准确率 | 是 | Phoenix + DeepEval | 规则型 |
| 工具调用 | 参数正确性 | 是 | Phoenix + DeepEval | 规则型 |
| 工具调用 | 多工具协同 | 是 | Phoenix | 轨迹型 |
| 记忆与上下文 | 短期上下文保持 | 是 | Phoenix + DeepEval | 规则型 + LLM Judge |
| 记忆与上下文 | 长期记忆召回 | 否，二期 | Phoenix | 专项样本 + Judge |
| 鲁棒性 | 自我纠错 | 是 | Phoenix + DeepEval | 规则型 + 轨迹型 |
| 鲁棒性 | 异常处理 | 是 | Phoenix + DeepEval | 规则型 |
| 效率成本 | 延迟 | 是 | Phoenix + Metrics | 指标统计 |
| 效率成本 | Token 消耗 | 是 | Phoenix | 指标统计 |
| 效率成本 | 交互步数 | 是 | Phoenix | 指标统计 + 轨迹型 |
| 安全合规 | 幻觉率（工具/结构） | 是，基础版 | Phoenix + DeepEval | 规则型 |
| 安全合规 | Prompt Injection / 越权拒绝 | 否，三期 | Phoenix + DeepEval | 专项安全 evaluator |

这张表表达了三件事：

1. **不是所有维度首期都一起做**
2. **首期先做最能落地、最有价值的**
3. **Phoenix 和 DeepEval 分工不同，但会共同覆盖核心能力**

---

## 4.2 首期必须交付的评测能力

首期必须交付的评测能力如下。

### A. 任务成功率
明确回答：

- 这条样本有没有完成目标
- 有没有 fallback
- 有没有至少给出一条有效 recommendation / note / route 结果

### B. 工具调用正确性
明确回答：

- 是否调用了应该调用的工具
- 是否误调了不应该调用的工具
- 参数是否足够正确

### C. 恢复与澄清能力
明确回答：

- 空结果后有没有恢复
- 缺位置时有没有澄清或恢复
- 路线缺参数时有没有走 guardrail
- 模糊意图时有没有合理澄清

### D. 结构正确性
明确回答：

- 输出是否满足 [app/agent/schemas.py](app/agent/schemas.py#L36-L45) 的结构契约
- recommendation item 类型是否正确

### E. 效率与步骤
明确回答：

- 耗时多少
- 走了多少步
- 调了多少次工具
- 是否出现明显绕弯或重复

### F. 场景质量
明确回答：

- eat_out 是否满足预算/口味/附近等约束
- cook_home 是否利用了食材
- route 是否包含关键路线信息
- clarify 是否真的在澄清关键分歧点

---

## 5. 每一类评测到底怎么做

这一节把“做什么”细化成“怎么做”。

---

## 5.1 任务达成度怎么做

## 5.1.1 任务成功率

### 定义

一次样本执行后，如果满足该场景的最低成功条件，则记为成功。

### 在 Smart-Eats 中的成功条件

按场景定义：

#### eat_out
满足以下条件中的主要条件：

- 最终输出为合法 FinalAnswer
- `recommendations` 非空
- recommendation 至少有一项是 `restaurant` 或可接受的 `note`
- 非 fallback

#### cook_home
满足以下条件中的主要条件：

- 最终输出为合法 FinalAnswer
- `recommendations` 非空
- recommendation 至少有一项是 `recipe` 或合理 note
- 非 fallback

#### route
满足以下条件中的主要条件：

- 成功调用 `plan_route` 并输出最终路线建议
- 若缺参数，则触发正确 guardrail 提示而非随机回答

#### clarify
满足以下条件中的主要条件：

- 没有硬 fallback
- 给出了针对关键分歧点的澄清问题

### 数据来源

- 最终输出 JSON
- trace 中的 final action
- 当前已有 metrics 中的 fallback / non_fallback 计数
- 内部 metrics API：[app/api/v1/internal.py](app/api/v1/internal.py#L9-L26)

### 实现位置

- 规则型 evaluator
- Phoenix experiment 聚合统计
- DeepEval regression 基础断言

### 最终产出指标

- success_rate
- non_fallback_rate
- scene_success_rate

---

## 5.1.2 完成质量

### 定义

任务虽然完成，但质量可能不同，因此需要进一步评估结果质量。

### 在 Smart-Eats 中如何判定

#### eat_out
看：

- 是否考虑预算
- 是否考虑品类
- 是否考虑附近 / 位置
- 推荐是否可执行

#### cook_home
看：

- 是否利用冰箱食材
- 菜谱是否可操作
- 是否给出合理 followups

#### route
看：

- 是否概括距离/时间/步骤
- 是否信息完整
- 是否没有无意义废话

#### clarify
看：

- 澄清问题是否聚焦
- 是否能帮助后续收敛

### 数据来源

- 最终 answer
- recommendation 内容
- followups / warnings

### 实现方式

- 规则型 evaluator：检查硬条件
- LLM Judge evaluator：评可执行性、相关性、自然性

### 执行位置

- Phoenix 为主
- DeepEval 可保留少量稳定规则

### 最终产出指标

- result_quality_score
- actionability_score
- constraint_satisfaction_rate

---

## 5.1.3 最优路径占比

### 定义

在成功完成任务的前提下，是否走了合理且相对高效的路径。

### 在 Smart-Eats 中如何近似定义

由于当前项目不是复杂办公 Agent，因此“最优路径”不采用过于抽象的定义，而采用近似规则：

- 是否在合理步数内完成
- 是否没有明显多余工具调用
- 是否没有重复进入无意义恢复路径
- 是否在工具成功后尽快收束到 final

### 数据来源

- trace 中的节点序列
- tool_calls
- step_index / events / planner 循环次数
- [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py#L173-L203)

### 执行位置

- Phoenix 二期 experiment

### 为什么不作为首期门禁

因为它需要先沉淀“参考路径模板”，否则容易误伤正常变化。

---

## 5.2 推理与规划能力怎么做

## 5.2.1 逻辑连贯性

### 定义

给定用户目标后，Agent 的行为是否遵循正确场景逻辑。

### 在 Smart-Eats 中的典型逻辑错误

- 问路线却搜菜谱
- 想在家做饭却搜餐厅
- 位置未解决却直接餐厅搜索失败结束
- 模糊输入本应澄清却直接 fallback

### 数据来源

- intent 判定结果
- trace 节点顺序
- tool 调用记录
- 最终输出类型

### 实现方式

- 规则型 evaluator：意图/场景与工具匹配关系
- 轨迹型 evaluator：路径顺序是否合理

### 执行位置

- Phoenix
- DeepEval 中保留少量高价值断言

---

## 5.2.2 动态调整 / Re-planning

### 定义

当工具返回异常、信息缺失或结果为空时，Agent 是否能调整策略，而不是立即崩溃。

### 当前项目已有可评估恢复路径

根据 [app/tests/test_smart_eats_tool_result_handler.py](app/tests/test_smart_eats_tool_result_handler.py#L48-L186)，当前已经有明确恢复语义：

- `search_restaurants` 空结果 -> 恢复上下文
- `search_restaurants` 缺位置 -> 恢复上下文
- `geocode_location` 失败 -> 记录定位错误
- `plan_route` 缺起点/终点 -> 走硬 guardrail
- `plan_route` 成功 -> 记录 route 并指导尽快 final

### 数据来源

- tool result handler 产出的上下文变化
- trace 中的 recovery path
- state.context / context_overrides

### 实现方式

- 规则型 evaluator：检查恢复状态是否建立
- 轨迹型 evaluator：检查是否继续尝试/收束得当

### 执行位置

- 首期 Phoenix + DeepEval 都做

### 首期必须覆盖的恢复场景

1. restaurant empty result
2. restaurant missing location
3. geocode not found
4. route missing origin
5. route missing destination
6. fridge empty best effort
7. clarify instead of blind guess

---

## 5.3 工具调用能力怎么做

## 5.3.1 工具选择准确率

### 定义

在当前任务下是否选对了工具。

### 当前工具清单

可直接参考 [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py#L82-L93)：

- `get_weather`
- `get_fridge_items`
- `search_recipes`
- `rag_search_recipes`
- `search_restaurants`
- `plan_route`
- `get_ip_location`
- `geocode_location`
- `get_user_info`

### 场景级工具选择规则

#### eat_out
通常允许或期待：

- `get_ip_location` / `geocode_location`
- `search_restaurants`
- 视情况使用 `get_user_info`

通常不应优先走：

- `search_recipes`
- `rag_search_recipes`
- `plan_route`（除非用户明确问路线）

#### cook_home
通常允许或期待：

- `get_fridge_items`
- `search_recipes`
- `rag_search_recipes`

通常不应优先走：

- `search_restaurants`
- `plan_route`

#### route
必须或高度期待：

- `plan_route`

通常不应主走：

- `search_recipes`
- `search_restaurants`

### 数据来源

- trace 工具调用列表
- state.tool_calls

### 实现方式

- 规则型 evaluator
- 按样本的 `tool_expectation` 判定 must_call / forbid_call

### 执行位置

- Phoenix
- DeepEval regression

---

## 5.3.2 参数正确性

### 定义

工具虽然被选对，但参数可能错，因此需要单独评。

### 当前项目最适合首期检查的参数项

#### search_restaurants
检查：

- query 是否从用户诉求中抽取到
- lat/lng 是否正确注入
- 是否没有脏字段

当前已有参数归一化测试信号：
- [app/tests/test_smart_eats_tool_result_handler.py](app/tests/test_smart_eats_tool_result_handler.py#L208-L216)

#### plan_route
检查：

- origin/destination 是否齐全
- route 成功后结构是否完整

#### geocode_location
检查：

- 输入地名是否被正确转为位置语义

### 数据来源

- 工具调用参数摘要
- trace span attributes

### 实现方式

- 规则型 evaluator
- 参数模板检查
- 特定字段 presence / absence 断言

### 执行位置

- 首期做基础版
- Phoenix 和 DeepEval 都可做

---

## 5.3.3 多工具协同

### 定义

复杂任务是否能正确串联多个工具，而不是只会单工具点射。

### 当前项目适合评估的协同链路

- geocode -> restaurant search
- fridge -> recipe search
- route missing info -> clarify / recover -> route final

### 数据来源

- trace 工具序列
- node sequence
- recovery path

### 实现方式

- 轨迹型 evaluator
- 对比“允许路径集合”

### 执行位置

- Phoenix 为主

---

## 5.4 记忆与上下文管理怎么做

## 5.4.1 短期上下文保持

### 定义

在一个会话或一组样本轮次里，能否保持用户前文提供的限制条件。

### 当前适合评估的上下文类型

- 预算
- 口味
- 地点
- 冰箱食材
- 用户刚刚澄清过的场景选择

### 数据来源

- 样本 history / turns
- 最终输出
- trace 中 context summary

### 实现方式

- 规则型 evaluator：约束是否继续体现在结果里
- LLM Judge：语义上是否仍遵守前文约束

### 执行位置

- 首期可选 3~5 条代表性多轮样本
- Phoenix 为主，DeepEval 只放少量稳定 case

---

## 5.4.2 长期记忆召回

### 首期结论

不进入首期主计划，只在文档中预留。

### 原因

当前首期评测体系更应该先把：

- tool use
- recovery
- output quality
- latency

这些业务收益最大的维度跑稳。

长期记忆评测需要额外补：

- 跨 session 数据集
- memory 命中/未命中可观测信号
- 偏好召回 evaluator

因此在本文档中只保留为二期专项。

---

## 5.5 鲁棒性与自我纠错怎么做

这是当前 Smart-Eats 最值得重点建设的评测能力之一。

---

## 5.5.1 异常处理

### 定义

面对缺信息、空结果、上游失败时，是否有合理的系统行为。

### 首期要覆盖的异常类型

1. missing_location
2. empty_result
3. geocode not_found
4. route missing_origin
5. route missing_destination
6. route upstream_failed
7. fridge empty

### 数据来源

- tool result
- context / context_overrides
- 最终输出

### 实现方式

- 规则型 evaluator
- 利用已有测试经验抽象判定规则

### 与现有代码的直接关联

这类规则可以直接从 [app/tests/test_smart_eats_tool_result_handler.py](app/tests/test_smart_eats_tool_result_handler.py#L48-L186) 提炼。

---

## 5.5.2 自我纠错 / 反射式恢复

### 定义

工具失败后是否继续走正确恢复路径，而不是硬停。

### 在 Smart-Eats 中的首期近似定义

由于当前并没有独立 reflection agent，因此“自我纠错”不定义成抽象思维能力，而定义成：

- 是否在失败后建立恢复上下文
- 是否继续尝试可行策略
- 是否在不能继续时给出合理说明

### 数据来源

- recovery path
- tool retry 次数
- restaurant_retries
- last_search_error / last_location_error

### 执行位置

- Phoenix 和 DeepEval 都覆盖

---

## 5.6 效率与成本怎么做

## 5.6.1 延迟

### 定义

从请求进入到 final 输出所需时间，以及中间关键节点耗时。

### 数据来源

- request root span duration
- tool span duration
- planner / writer 节点 duration
- 现有服务日志与 metrics 辅助视角

### 接入位置

- [app/api/v1/chat.py](app/api/v1/chat.py#L174-L225)：request root span
- [app/agent/graph.py](app/agent/graph.py)：节点 span
- [app/agent/tools/](app/agent/tools/)：tool span

### 产出指标

- total_latency_ms
- tool_latency_ms
- planner_latency_ms
- writer_latency_ms
- p50 / p95

### 执行位置

- Phoenix 主统计

---

## 5.6.2 Token 消耗

### 定义

一次任务消耗的输入/输出 token 和总成本。

### 数据来源

- 模型调用 usage
- trace 中的 generation attributes

### 产出指标

- prompt_tokens
- completion_tokens
- total_tokens
- per_scene_avg_tokens

### 执行位置

- Phoenix 主统计

---

## 5.6.3 交互步数

### 定义

完成任务走了多少步、调了多少工具、有没有循环过多。

### 数据来源

- step_index
- tool_calls
- events
- trace node count

### 产出指标

- avg_steps_per_task
- avg_tool_calls_per_task
- max_steps_hit_rate
- repeated_tool_pattern_rate

### 执行位置

- Phoenix 主统计
- 少量阈值可进 DeepEval

---

## 5.7 安全与合规怎么做

## 5.7.1 首期做基础安全评测

首期不追求红队级安全评测，但至少要有基础安全面。

### 首期可做内容

- 工具白名单检查：是否出现不存在工具调用
- 输出结构幻觉检查：是否生成非法 recommendation 类型
- 场景越界检查：例如 cook_home 不应凭空输出路线规划结果

### 数据来源

- tool calls
- final output schema
- evaluator 白名单

### 执行位置

- DeepEval：基础规则
- Phoenix：统计异常率

---

## 5.7.2 三期再做专项安全集

后续补：

- prompt injection 样本
- tool misuse 样本
- destructive instruction refusal 样本
- 越权请求拒绝样本

首期不阻塞主方案推进。

---

## 6. 代码接入总路线

这一节回答“代码改哪些地方”。

---

## 6.1 需要新增的目录与文档

### 新增目录

按前文目录设计，首期先新增：

```text
evals/
  datasets/
    golden/
    regression/
    stress/
  evaluators/
    shared/
    business/
    trajectory/
    safety/
  experiments/
    phoenix/
  adapters/
  reports/
```

### 新增文档

已经具备：

- [docs/phoenix-deepeval-evaluation-architecture.md](docs/phoenix-deepeval-evaluation-architecture.md)
- [docs/evaluation-directory-and-module-design.md](docs/evaluation-directory-and-module-design.md)
- [docs/evaluation-dataset-and-evaluator-design.md](docs/evaluation-dataset-and-evaluator-design.md)
- [docs/evaluation-implementation-plan.md](docs/evaluation-implementation-plan.md)

---

## 6.2 需要改造的运行代码挂载点

首期改造挂载点如下。

### A. request 根入口

文件： [app/api/v1/chat.py](app/api/v1/chat.py#L174-L225)

要做的事：

- 建立 Phoenix request root span
- 记录 session_id、agent_type、quick_intent、是否有设备位置
- 记录请求开始/结束时间
- 关联 trace_id

### B. graph 主流程

文件： [app/agent/graph.py](app/agent/graph.py)

要做的事：

- 给关键节点打 span
- 记录节点进入/退出
- 记录 planner / writer / final 阶段
- 记录 recovery path、fallback、clarify
- 记录 step 数、tool decision

### C. 业务 Agent 层

文件： [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py)

要做的事：

- 记录 intent、intent_confidence、need_clarify
- 记录 location_source、task_stage、tool_plan
- 记录 context_overrides 的关键变化
- 记录 tool_result_handler 的恢复事件

### D. 工具层

目录： [app/agent/tools/](app/agent/tools/)

要做的事：

- 每个关键工具输出 span
- 记录参数摘要
- 记录成功/失败
- 记录结果条数、命中缓存与否、关键错误码

### E. metrics 层

文件： [app/agent/metrics.py](app/agent/metrics.py#L13-L35)

要做的事：

- 保留现有 lightweight metrics
- 补充与 Phoenix 可对齐的统计口径
- 不把它扩展成 experiment 平台

---

## 6.3 需要新增的适配层

### 新增目录

- `evals/adapters/`

### 首期需要的 adapter 能力

1. 样本 -> API 请求映射
2. 样本 -> 运行上下文映射
3. SSE stream -> final answer 提取
4. trace / output -> evaluator 输入标准化

### 为什么必须先做 adapter

因为当前 [scripts/replay_eval.py](scripts/replay_eval.py#L23-L56) 把调用、提取、判定耦合在一起，后续如果不抽 adapter：

- Phoenix experiment 无法复用
- DeepEval regression 无法复用
- dataset 设计会被 HTTP 接口细节绑死

---

## 7. 详细实施分阶段计划

下面进入最核心的实施步骤。每一阶段都包括：

- 目标
- 要做的改动
- 会产生哪些评测能力
- 验收标准

---

## Phase 0：准备与基线盘点

### 目标

在不影响现有功能的前提下，为评测体系做基线盘点和目录准备。

### 要做的事

#### 0.1 建立目录骨架

新增：

- `evals/datasets/golden/`
- `evals/datasets/regression/`
- `evals/datasets/stress/`
- `evals/evaluators/shared/`
- `evals/evaluators/business/`
- `evals/evaluators/trajectory/`
- `evals/evaluators/safety/`
- `evals/experiments/phoenix/`
- `evals/adapters/`
- `evals/reports/`

#### 0.2 迁移 replay 样本为正式 seed

现有：
- [app/tests/fixtures/replay_cases.json](app/tests/fixtures/replay_cases.json#L1-L65)

首期动作：
- 复制为 `evals/datasets/regression/` 下正式样本
- 不删除旧文件
- 保持旧脚本兼容

#### 0.3 梳理现有 metrics 基线

对接：
- [docs/agent-metrics.md](docs/agent-metrics.md)
- [app/api/v1/internal.py](app/api/v1/internal.py#L9-L26)

目标：
- 明确当前已有 fallback_rate 能力
- 记录当前 baseline 值

### 产出

- 目录结构 ready
- regression seeds ready
- baseline metrics ready

### 验收标准

- 目录创建完成
- replay seeds 可被新路径读取
- 现有 replay 和 metrics 功能不受影响

---

## Phase 1：Phoenix tracing 首批接入

### 目标

把“看不见 agent 行为”变成“能看完整链路”。

### 要做的事

#### 1.1 新增 tracing 模块

建议新增：
- `app/agent/tracing.py`

职责：
- 初始化 Phoenix / OpenInference
- 封装 root span / child span
- 封装常见 attribute 写入方法

#### 1.2 在 chat API 建 request root span

位置： [app/api/v1/chat.py](app/api/v1/chat.py#L174-L225)

记录字段：
- session_id
- user_id 是否存在
- quick_intent
- has_device_location
- trace_id
- request_start / request_end

#### 1.3 在 graph 主流程加节点 span

位置： [app/agent/graph.py](app/agent/graph.py)

至少覆盖：
- observe / context load
- planner
- tool dispatch
- tool result handling
- writer
- final output
- fallback / clarify / stop

#### 1.4 在 smart_eats agent 层加语义事件

位置： [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py)

至少记录：
- intent
- intent_confidence
- need_clarify
- task_stage
- location_source
- recovery_path
- tool_plan

#### 1.5 在工具层加 span

目录： [app/agent/tools/](app/agent/tools/)

至少先接：
- `search_restaurants`
- `geocode_location`
- `get_ip_location`
- `get_fridge_items`
- `search_recipes`
- `rag_search_recipes`
- `plan_route`

记录内容：
- 工具名
- 参数摘要
- 结果摘要
- 错误码
- 耗时

### 这一步实现后我们能评什么

- 延迟
- 步数
- 工具调用次数
- 路径合理性
- 恢复路径
- fallback/clarify 分布

### 验收标准

- 能在 Phoenix 中看到一次请求的完整 trace
- 能看到 graph 节点与工具调用顺序
- 能区分成功、fallback、clarify、异常结束
- 至少一条 eat_out、一条 cook_home、一条 route 样本有完整 trace

---

## Phase 2：样本模型与数据集首批落地

### 目标

把“零散 replay case”变成“正式可运营 dataset”。

### 要做的事

#### 2.1 设计正式样本字段

首期字段分为：
- identity
- input
- context
- expectations
- tags
- metadata

字段语义详见：
- [docs/evaluation-dataset-and-evaluator-design.md](docs/evaluation-dataset-and-evaluator-design.md)

#### 2.2 组建第一批 Golden Dataset

建议 10~15 条，覆盖：
- eat_out 基础推荐
- cook_home 基础推荐
- route 基础成功
- clarify
- chat

#### 2.3 组建第一批 Regression Dataset

建议 10~20 条，优先覆盖：
- 位置即目标
- 空结果恢复
- 缺位置恢复
- geocode 错误恢复
- route missing origin
- route missing destination
- route upstream_failed
- fridge empty
- 模糊意图澄清
- chat 不误走重工具链

#### 2.4 组建第一批 Stress Dataset

建议 10 条左右，先不追求多。

可包含：
- 多约束复合查询
- 歧义地名
- 多轮改需求
- 预算和距离冲突

### 这一步实现后我们能评什么

- 任务成功率
- 场景覆盖率
- 恢复样本覆盖率
- 样本切片分析

### 验收标准

- golden / regression / stress 三层数据集均存在
- 每层至少有最小可用样本数
- 样本都有 scene、capability、risk 标签
- replay seeds 已纳入 regression 体系

---

## Phase 3：Evaluator 首批实现

### 目标

把“我们知道要评什么”变成“系统知道怎么判”。

### 首期 evaluator 交付清单

必须实现四类 evaluator，但数量要控制。

---

### 3.1 结构型 evaluator

#### 要实现的检查项

1. 输出是否可解析为 FinalAnswer
2. recommendations 是否是 list
3. followups / warnings 是否是 list
4. recommendation item 是否属于 recipe / restaurant / note

#### 它覆盖哪些评测

- 结构正确性
- 基础幻觉防御
- 最低输出契约

#### 执行位置

- DeepEval 必做
- Phoenix 可复用

---

### 3.2 规则型 evaluator

#### 首期至少实现以下规则组

##### R1. 非 fallback evaluator
检查：
- 预期 non_fallback 的样本不应输出 fallback

##### R2. 场景-工具匹配 evaluator
检查：
- eat_out 不应误走 recipe 主路径
- cook_home 不应误走 restaurant 主路径
- route 必须走 plan_route

##### R3. 恢复路径 evaluator
检查：
- empty_result 后是否建立恢复上下文
- missing_location 后是否建立恢复上下文
- geocode not_found 后是否记录 last_location_error
- route 缺参数时是否触发正确 note

##### R4. 输出类型 evaluator
检查：
- eat_out 至少包含 restaurant/note
- cook_home 至少包含 recipe/note
- route 成功样本输出应包含路线关键信息

##### R5. 约束命中 evaluator
检查：
- 预算是否被考虑
- 品类是否被考虑
- 食材是否被利用

#### 它覆盖哪些评测

- 任务达成
- 工具选择
- 参数合理性（基础版）
- 异常处理
- 自我纠错

#### 执行位置

- DeepEval 核心门禁
- Phoenix experiments 复用

---

### 3.3 LLM Judge evaluator

#### 首期只做少量高价值 judge

建议首期只做 2 组：

##### J1. 可执行性 / Actionability Judge
评估：
- 推荐是否可执行
- 回答是否真的帮助用户下一步行动

##### J2. 澄清质量 Judge
评估：
- 澄清问题是否聚焦关键分歧点
- 是否比直接 fallback 更好

#### 它覆盖哪些评测

- 完成质量
- 回答自然度
- 澄清质量

#### 执行位置

- Phoenix 为主
- 不建议首期强依赖它作为 CI 硬门槛

---

### 3.4 轨迹型 evaluator

#### 首期建议先做 2 组

##### T1. 工具路径合理性 evaluator
检查：
- 是否调用了不该调用的工具
- 是否在合理顺序内调用工具
- 是否在成功后仍继续无意义循环

##### T2. 恢复路径执行 evaluator
检查：
- 失败后是否进入恢复路径
- 恢复后是否继续推进任务
- 是否在不可恢复时合理收束

#### 它覆盖哪些评测

- 逻辑连贯性
- 动态调整
- 多工具协同
- 最优路径近似

#### 执行位置

- Phoenix 为主

### 验收标准

- 结构型 evaluator 全量可跑
- 至少 5 组规则型 evaluator 可跑
- 至少 2 组 LLM Judge evaluator 可跑
- 至少 2 组轨迹型 evaluator 可跑
- evaluator 能被 Phoenix 和 DeepEval 分别装配

---

## Phase 4：Phoenix experiments 首批落地

### 目标

把“有 trace、有样本、有 evaluator”变成“能比较版本”。

### 要做的事

#### 4.1 实验装配入口

新增目录：
- `evals/experiments/phoenix/`

职责：
- 选择 dataset
- 选择 evaluator 组合
- 选择被评测 target
- 组织一次完整 experiment

#### 4.2 首批要跑的实验集

建议至少有三组标准实验：

##### E1. Golden 全量实验
目标：看核心场景整体表现

##### E2. Regression 恢复实验
目标：看恢复路径和历史 bug 是否稳

##### E3. Stress 探索实验
目标：看复杂约束和长尾问题趋势

#### 4.3 首批需要输出的实验指标

- overall success_rate
- scene_success_rate
- fallback_rate
- clarify_rate
- avg_steps
- avg_tool_calls
- p50/p95 latency
- avg tokens
- recovery_success_rate
- wrong_tool_rate

#### 4.4 首批实验切片

按标签切片至少支持：

- scene
- capability
- risk_tags
- dataset_type

### 验收标准

- 能在 Phoenix 中运行 Golden / Regression / Stress 三类实验
- 每组实验都有可读结果
- 能按场景看成功率与 fallback_rate
- 至少能比较两个不同版本 target 的结果差异

---

## Phase 5：DeepEval 回归门禁接入

### 目标

把“我们知道问题在哪”变成“以后改坏了会被拦住”。

### 要做的事

#### 5.1 新建测试目录

新增：

```text
tests/
  agent_eval/
    regression/
    smoke/
    fixtures/
```

#### 5.2 首批回归集范围

建议 10~20 条，不要更多。

必须优先纳入：

1. eatout-location-as-target
2. eatout-empty-result-recovery
3. route-followup
4. cook-home-query
5. clarify-scene
6. chat-greeting
7. route missing origin
8. route missing destination
9. geocode not_found recovery
10. fridge empty best effort

#### 5.3 首批 CI 门禁项

建议首期只拦截下面这些稳定项：

- 输出结构合法
- 预期 non_fallback 的样本不能 fallback
- route 样本必须走 `plan_route` 或 guardrail
- 恢复场景必须出现预期恢复信号
- 关键场景不允许误调用明显错误工具

#### 5.4 本地与 CI 分层策略

##### 本地 smoke
- 5 条左右
- 快速验证
- 开发者几分钟内跑完

##### CI regression
- 10~20 条
- 强规则为主
- LLM Judge 尽量少或不作为硬门槛

### 验收标准

- 本地可运行 smoke suite
- CI 可运行 regression suite
- 至少能稳定拦截 fallback 回退、route 误行为、恢复路径失效等问题

---

## Phase 6：报表与运营闭环

### 目标

把评测结果转成可持续运营的质量信号，而不是一次性实验输出。

### 要做的事

#### 6.1 统一报告出口

保留并逐步演化：
- [scripts/agent_eval_dashboard.py](scripts/agent_eval_dashboard.py#L13-L38)

将其从“replay + metrics 合并脚本”演化成“统一评测 scorecard 入口”。

#### 6.2 统一报告内容

至少输出：

- online fallback_rate
- offline regression success_rate
- golden success_rate
- recovery_success_rate
- route success_rate
- avg_steps
- p95 latency
- avg_token_usage

#### 6.3 线上问题回流流程

建立固定流程：

1. 线上出现 bad case
2. 在 Phoenix 中定位 trace
3. 标注失败类型
4. 决定加入 regression 还是 stress
5. 修复后进入下一轮验证

### 验收标准

- 评测结果不只存在于 Phoenix 页面里
- 有统一 scorecard 输出
- 有问题回流到数据集的固定流程

---

## 8. 首期详细文件级实施清单

下面按文件列出首期要改什么。

---

## 8.1 运行层

### [app/api/v1/chat.py](app/api/v1/chat.py#L174-L225)

首期改动：
- request root span
- trace attributes: session_id / quick_intent / has_device_location / agent_type
- 请求总耗时记录

### [app/agent/graph.py](app/agent/graph.py)

首期改动：
- planner / writer / final / tool dispatch span
- recovery / fallback / clarify 事件埋点
- step 数与路径信息埋点

### [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py)

首期改动：
- intent / confidence / clarify 埋点
- task_stage / tool_plan / recovery_path 埋点
- tool_result_handler 恢复事件埋点

### [app/agent/tools/](app/agent/tools/)

首期改动：
- 关键工具 span
- 参数与结果摘要埋点
- error_code 埋点

### [app/agent/metrics.py](app/agent/metrics.py#L13-L35)

首期改动：
- 保持现有 metrics 不破坏
- 补充与 Phoenix 对齐的命名和统计说明

---

## 8.2 评测资产层

### `evals/datasets/`

首期新增：
- regression 初版样本
- golden 初版样本
- stress 初版样本

### `evals/evaluators/shared/`

首期新增：
- evaluator 返回结构定义
- 公共标签与字段处理
- 输出结构校验工具

### `evals/evaluators/business/`

首期新增：
- 非 fallback evaluator
- scene quality evaluator
- constraint satisfaction evaluator

### `evals/evaluators/trajectory/`

首期新增：
- tool path evaluator
- recovery path evaluator

### `evals/evaluators/safety/`

首期新增：
- tool whitelist evaluator
- output type legality evaluator

### `evals/adapters/`

首期新增：
- sample -> request adapter
- stream -> final result extractor
- trace -> evaluator input normalizer

### `evals/experiments/phoenix/`

首期新增：
- Golden 实验入口
- Regression 实验入口
- Stress 实验入口

---

## 8.3 测试与命令入口层

### [scripts/replay_eval.py](scripts/replay_eval.py#L23-L56)

首期改动：
- 支持读取 `evals/datasets/...`
- 内部逐步调用 adapter，而不是自己包揽全部逻辑

### [scripts/agent_eval_dashboard.py](scripts/agent_eval_dashboard.py#L13-L38)

首期改动：
- 作为统一报告入口保留
- 后续逐步支持 Phoenix/DeepEval 汇总

### `tests/agent_eval/`

首期新增：
- smoke suite
- regression suite

---

## 9. 本地执行方式与团队工作流

实施计划必须回答“开发时怎么用”。

---

## 9.1 开发者日常工作流

### 修改前

- 先看 Phoenix 中当前相关场景基线
- 明确这次改动影响哪些 scene / tool / recovery path

### 修改中

- 本地跑 smoke suite
- 针对相关 dataset 做 replay 或小规模 experiment

### 修改后

- 看 Phoenix experiment 对比结果
- 如果修复了历史 bug，则补 regression case
- 如果引入了新长尾问题，则补 stress case

---

## 9.2 评测资产维护工作流

### 新 bug 出现时

1. 先记录 trace
2. 标注失败类型
3. 判断归类：regression 还是 stress
4. 写 expectation
5. 加 evaluator 覆盖

### 旧 case 失效时

1. 检查是业务规则变了还是样本过时
2. 若业务规则变更，更新 expectation
3. 若样本失真，移出回归门禁或降级到 stress

---

## 9.3 本地运行建议

建议提供三档运行方式：

### S1：快速自测

适用：开发中随手验证

内容：
- 3~5 条 smoke regression
- 不跑重型 judge

### S2：场景验证

适用：改了某个场景逻辑后

内容：
- 只跑相关 scene 的 dataset
- 看 Phoenix trace + 结果聚合

### S3：完整回归

适用：合并前 / 发布前

内容：
- 运行 regression suite
- 查看统一 scorecard

---

## 10. 首期验收清单

首期不是“做完一点算一点”，而是必须满足可验收标准。

---

## 10.1 Phoenix 接入验收

必须满足：

- 能看到 request root trace
- 能看到 graph 节点 span
- 能看到工具调用 span
- 能看到 fallback / clarify / recovery 事件
- 能看到 latency / steps / tool count

---

## 10.2 数据集验收

必须满足：

- 有 golden / regression / stress 三层
- replay case 已迁入 regression seeds
- 每条样本有 id、scene、expectation、source

---

## 10.3 evaluator 验收

必须满足：

- 结构型 evaluator 可稳定运行
- 至少 5 组规则型 evaluator 可运行
- 至少 2 组轨迹型 evaluator 可运行
- 至少 1~2 组 LLM Judge evaluator 可运行

---

## 10.4 DeepEval 验收

必须满足：

- 本地 smoke 可跑
- regression suite 可跑
- 能拦截 fallback 回退
- 能拦截 route 相关明显退化
- 能拦截关键恢复路径失效

---

## 10.5 业务价值验收

必须满足：

- 团队能明确知道当前 eat_out / cook_home / route 的 success_rate
- 团队能定位至少 3 类典型失败模式
- 团队能通过 regression suite 防止至少 3 类历史 bug 复发
- 团队能对一次 prompt/graph 调整做前后实验对比

---

## 11. 风险与控制策略

实施过程中最容易踩的坑有以下几类。

---

## 11.1 一开始评测铺得太大

### 风险

- evaluator 太多
- judge 太多
- CI 太慢
- 团队很快放弃维护

### 控制策略

- 首期只做高价值 evaluator
- 回归集控制在 10~20 条
- stress 不进 CI

---

## 11.2 tracing 过重影响运行

### 风险

- span 太多
- payload 太大
- 工具原始返回全量记录导致成本高

### 控制策略

- 只记录摘要，不记录全量结果
- 优先记录关键字段与计数
- 对高频工具控制 span attributes 大小

---

## 11.3 evaluator 定义过于抽象

### 风险

- 评测项听起来高级，但没人知道失败意味着什么

### 控制策略

每个 evaluator 必须明确：
- 评哪类样本
- 依赖哪些字段
- 输出什么结论
- 失败意味着什么问题

---

## 11.4 把 LLM Judge 当唯一标准

### 风险

- 波动大
- 成本高
- 可解释性差

### 控制策略

- 先规则后 judge
- judge 用于补足语言质量，不替代硬规则
- CI 不首期强依赖 judge

---

## 12. 最终交付物清单

如果按本文档完整推进，最终应交付以下内容。

### 文档

- [docs/phoenix-deepeval-evaluation-architecture.md](docs/phoenix-deepeval-evaluation-architecture.md)
- [docs/evaluation-directory-and-module-design.md](docs/evaluation-directory-and-module-design.md)
- [docs/evaluation-dataset-and-evaluator-design.md](docs/evaluation-dataset-and-evaluator-design.md)
- [docs/evaluation-implementation-plan.md](docs/evaluation-implementation-plan.md)

### 代码结构

- `evals/datasets/...`
- `evals/evaluators/...`
- `evals/adapters/...`
- `evals/experiments/phoenix/...`
- `tests/agent_eval/...`
- `app/agent/tracing.py`

### 评测能力

- success_rate
- non_fallback_rate
- scene_success_rate
- recovery_success_rate
- wrong_tool_rate
- avg_steps / avg_tool_calls
- latency / token metrics
- Golden / Regression / Stress 实验
- DeepEval regression gate

### 流程能力

- 线上 bad case 回流到 dataset
- 修 bug 后进入 regression
- prompt / graph 改动可实验对比
- 发布前可自动回归

---

## 13. 推荐执行顺序总结

如果只看一句话执行顺序，建议按下面顺序推进：

1. **先建 `evals/` 目录与 dataset seed**
2. **再接 Phoenix tracing，看清链路**
3. **再做结构型 + 规则型 evaluator**
4. **再跑 Phoenix experiments**
5. **再把最关键 10~20 条回归集接到 DeepEval**
6. **最后再扩充 judge、轨迹最优路径、安全专项**

这条路线的好处是：

- 每一步都有独立价值
- 不需要一次性大改全项目
- 可以尽快产出第一批可见评测结果
- 能尽早形成“发现问题 -> 固化问题 -> 防止复发”的闭环

---

## 14. 本文档的结论

这份实施计划的核心不是再讲抽象评测理念，而是明确：

- **我们要评哪些能力**
- **每种能力如何评**
- **依赖哪些数据源**
- **放在哪个系统里跑**
- **具体改哪些文件和目录**
- **按什么顺序上线**
- **做完如何验收**

对于当前 Smart-Eats 项目，最现实、最有价值的落地路线是：

- 以 **任务达成、工具调用、恢复路径、结构契约、效率指标** 为首期重点
- 以 **Phoenix** 建立观察与实验能力
- 以 **DeepEval** 建立回归守门能力
- 以 **dataset + evaluator** 建立长期可运营的评测资产

做到这一步之后，Smart-Eats 的 Agent 评测体系就不再只是几份脚本和一些日志指标，而会变成一套真正可持续演进的工程体系。

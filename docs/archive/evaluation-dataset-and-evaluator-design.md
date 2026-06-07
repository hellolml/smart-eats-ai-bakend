> ⚠️ **已过期** — 本文档设计的是理想数据集三层分层（Golden/Regression/Stress）和 Evaluator 四层分类，与当前实现不匹配。当前使用 `category`（normal/boundary/tool_failure/safety/regression）+ 扁平 10 个 Evaluator。仅供参考，请以 `evaluation-usage-guide.md` 和实际代码为准。

# Smart-Eats Agent 数据集与 Evaluator 设计

## 1. 文档目标

本文档是前两份评测方案文档的继续落地：

- 总体架构： [docs/phoenix-deepeval-evaluation-architecture.md](docs/phoenix-deepeval-evaluation-architecture.md)
- 目录与模块： [docs/evaluation-directory-and-module-design.md](docs/evaluation-directory-and-module-design.md)

前两份文档回答了：

- 为什么采用 Phoenix + DeepEval
- 评测体系的整体职责分层
- 目录与模块应该如何组织

本文档回答的是：

- 数据集样本应该长什么样
- 标签体系应该如何设计
- evaluator 应该分成哪些层次
- 哪些指标适合 Phoenix，哪些适合 DeepEval
- Smart-Eats 第一批回归集应该如何挑选

本文档仍然以**设计原则、结构定义、评测口径、资产运营方式**为主，尽量不写过多实现细节。

---

## 2. 当前业务能力与评测对象范围

在设计 dataset 和 evaluator 之前，先明确当前项目到底在评什么。

从 [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py#L82-L93) 和 [app/agent/schemas.py](app/agent/schemas.py#L36-L88) 可以看出，当前 Agent 的核心能力主要围绕以下几类：

### 2.1 核心意图类型

- `eat_out`：外出就餐推荐
- `cook_home`：在家做饭建议
- `route`：路线规划
- `chat`：轻量闲聊
- `unknown`：需要澄清或暂时无法归类

### 2.2 关键工具能力

- 定位与地理解析：`get_ip_location`、`geocode_location`
- 餐厅搜索：`search_restaurants`
- 菜谱搜索：`search_recipes`、`rag_search_recipes`
- 冰箱库存：`get_fridge_items`
- 路线规划：`plan_route`
- 用户信息：`get_user_info`
- 天气：`get_weather`

### 2.3 最终输出结构

当前最终回答结构由 [app/agent/schemas.py](app/agent/schemas.py#L36-L45) 定义，主要包括：

- `recommendations`
- `followups`
- `warnings`

其中 `recommendations` 又可能是：

- 菜谱推荐
- 餐厅推荐
- note 类提示

因此，当前评测对象不能只看“回答像不像人话”，还必须看：

- 意图是否识别正确
- 工具是否调用合理
- 最终输出结构是否合规
- 推荐内容是否符合业务目标
- 异常或缺信息时是否走了正确恢复路径

---

## 3. 设计原则

## 3.1 样本是长期资产，不是一次性测试输入

dataset 不应被当成“为了跑测试而写的一批 JSON”，而应被视为长期资产。

这意味着样本必须满足：

- 可理解：后来的人能看懂它在测什么
- 可复现：同一输入能重复跑
- 可标注：能分场景、分风险、分优先级
- 可迁移：能同时被 Phoenix experiment 和 DeepEval regression 使用

---

## 3.2 样本定义与执行环境解耦

样本应表达“业务场景”和“期望行为”，而不是写死某次 HTTP 请求的所有细节。

例如，比起直接把某次接口请求全过程原样复制进样本，更应关注：

- 用户问题是什么
- 预置上下文是什么
- 期望走什么意图/路径
- 期望最终结果满足哪些条件

这样样本才能在未来复用于：

- 本地 replay
- Phoenix experiment
- DeepEval regression
- 手工分析与 case review

---

## 3.3 先设计“可判定条件”，再设计“理想答案”

很多 agent 场景没有唯一标准答案，因此 dataset 设计不能强依赖“标准文本”。

比起要求模型必须输出某一段固定话术，更现实的方式是先定义：

- 必须满足哪些条件
- 不允许出现哪些错误
- 应该包含哪些关键信息
- 应该触发哪些行为或工具

对于 Smart-Eats 这类推荐型 Agent，这种“约束式判定”比“完全精确匹配文本”更稳。

---

## 3.4 区分结果评测与过程评测

同一个样本，往往可以同时有两套判定：

- **结果判定**：最终回答是否合格
- **过程判定**：执行轨迹是否合理

例如“推荐附近餐厅”这个场景：

- 结果判定关注有没有给出可执行推荐
- 过程判定关注有没有先解决定位问题、有没有误走做饭流程

因此 evaluator 设计不能只盯最终文本。

---

## 4. 数据集资产分层设计

建议正式数据集继续沿用前文的三层结构，但在本文件中把用途定义得更具体。

---

## 4.1 Golden Dataset

### 定义

Golden dataset 是“核心能力样本集”，用于表达当前版本必须稳定支持的典型场景。

### 作用

- 作为版本对比基线
- 用于离线 experiment 的核心样本池
- 用于对外说明 Agent 当前覆盖能力

### 特点

- 场景典型
- 期望行为明确
- 样本规模适中
- 波动尽量小

### Smart-Eats 建议内容

建议覆盖：

- 外出吃饭推荐
- 在家做饭推荐
- 路线规划
- 轻量闲聊
- 模糊意图下的澄清

这类样本适合既被 Phoenix 使用，也能抽其中一部分进入 DeepEval。

---

## 4.2 Regression Dataset

### 定义

Regression dataset 是“历史出过问题、以后不能再犯”的样本集。

### 作用

- 优先进入 DeepEval regression suite
- 用于发布门禁
- 用于验证已修复缺陷不会复发

### 特点

- 风险高
- 背景明确
- 判定条件清晰
- 维护优先级高

### Smart-Eats 建议内容

优先围绕当前已有 replay 样本和工具恢复逻辑补齐。例如：

- 地点既是用户位置又是搜索目标的场景
- 餐厅搜索空结果后的恢复场景
- 路线规划缺起点/终点场景
- 冰箱为空时的最佳努力回复场景
- 模糊意图需要澄清的场景

现有 [app/tests/fixtures/replay_cases.json](app/tests/fixtures/replay_cases.json#L1-L65) 可以直接作为第一批 regression seeds。

---

## 4.3 Stress Dataset

### 定义

Stress dataset 是用于探索边界与脏场景的样本集。

### 作用

- 在 Phoenix 中做批量 experiment
- 用来观察长尾失败模式
- 用于改 prompt / graph 后的稳定性分析

### 特点

- 不一定适合直接进 CI
- 允许波动更大
- 更偏探索和诊断

### Smart-Eats 建议内容

例如：

- 多约束复合查询
- 地名歧义输入
- 中英混杂 / 口语输入
- 多轮中途改需求
- 位置、预算、口味同时存在冲突

---

## 5. 单条样本的数据模型设计

本节不强调具体实现代码，而强调字段语义。

建议每条样本都围绕“身份、输入、上下文、期望、标签、来源”六个维度设计。

---

## 5.1 身份字段

用于唯一标识和管理样本。

建议至少包含：

- `id`：样本唯一 ID
- `title`：样本简述
- `dataset_type`：golden / regression / stress
- `priority`：p0 / p1 / p2
- `enabled`：是否启用

### 设计原则

- `id` 要稳定，不要带时间戳
- `title` 面向人读，不要过度技术化
- `priority` 用于决定是否进回归门禁

---

## 5.2 输入字段

用于描述用户到底问了什么。

建议至少包含：

- `input.message`：用户本轮输入
- `input.agent_type`：请求使用的 agent 类型（如果适用）
- `input.scene_hint`：可选业务场景提示

对于后续多轮样本，可扩展：

- `input.history`：前序对话
- `input.turns`：多轮输入序列

### 设计原则

- 单轮样本优先简单清晰
- 多轮样本只在确有必要时引入
- 不要把运行时无关噪声写进输入字段

---

## 5.3 预置上下文字段

用于表达在执行前已知的环境信息。

建议按业务语义组织，而不是复制运行时内部状态。

可包括：

- `context.user_profile`：口味、预算、禁忌等
- `context.location`：预置地理信息
- `context.fridge_items`：冰箱库存
- `context.client_ip`：仅在需要测试定位链路时使用
- `context.weather`：需要时可显式注入
- `context.mock_tool_state`：测试特定工具返回时使用

### 设计原则

- 尽量写业务前置条件，不直接暴露内部 ChatState
- 仅在确实影响行为时才加入上下文
- 同一个场景里，能由工具自然获取的信息不要重复塞太多

---

## 5.4 期望字段

这是最关键的一组字段，用来定义“什么算通过”。

建议拆成四层：

### 5.4.1 intent_expectation

用于判定意图层行为，例如：

- 期望意图属于哪些候选集合
- 是否允许 unknown
- 是否应触发澄清
- 是否不应误判到其他场景

这与 [app/agent/schemas.py](app/agent/schemas.py#L71-L88) 的 intent 结构相对应。

### 5.4.2 tool_expectation

用于判定工具层行为，例如：

- 必须调用哪些工具
- 禁止调用哪些工具
- 可以接受的工具序列范围
- 是否应该出现恢复路径

这与 [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py#L82-L101) 中的工具集合和错误码体系相对应。

### 5.4.3 output_expectation

用于判定最终回答结构与内容，例如：

- 是否必须非 fallback
- 推荐列表是否不能为空
- 推荐类型应为 restaurant / recipe / note 哪一类
- 是否必须包含 followups
- 是否必须包含 warnings
- 是否必须提到预算 / 距离 / 路线关键要素

这与 [app/agent/schemas.py](app/agent/schemas.py#L36-L45) 定义的输出结构直接对应。

### 5.4.4 recovery_expectation

用于判定异常与缺信息场景，例如：

- 是否应进行澄清而不是直接失败
- 是否应走 best-effort 回答
- 是否应在空结果后进行恢复尝试
- 是否应给出缺少起点/终点的明确提示

这与现有工具恢复逻辑非常贴近，例如 [app/tests/test_smart_eats_tool_result_handler.py](app/tests/test_smart_eats_tool_result_handler.py#L48-L186) 中已经覆盖了不少恢复分支。

---

## 5.5 标签字段

标签是为了让样本可切片、可分析、可运营。

建议每条样本至少包含以下标签维度：

- `scene`：eat_out / cook_home / route / chat / clarify
- `capabilities`：tool_use / no_tool / multi_turn / fallback_recovery / location / fridge / route
- `risk_tags`：wrong_tool / hallucination / empty_result / missing_location / early_stop / low_confidence
- `source`：manual / replay / staging / prod / bugfix
- `stage`：golden / regression / stress

### 设计原则

- 标签要少而稳定
- 标签优先表达“为什么要关注这条样本”
- 不要把临时注释塞成标签

---

## 5.6 来源与运营字段

用于长期维护样本，而不是直接参与运行。

建议包括：

- `source_note`：样本来源说明
- `owner`：维护人或责任模块
- `created_from`：来自哪个线上问题、脚本、讨论或 bugfix
- `notes`：补充说明

### 设计原则

这类字段可以帮助未来判断：

- 这个 case 为什么存在
- 它还能不能删
- 它是否已经过时

---

## 6. 样本判定口径设计

不是每个样本都需要同样严格的判定方式。

建议定义三种判定口径。

---

## 6.1 精确约束型

适用于：

- 路线规划缺参数
- 明确错误提示
- 输出结构必须满足某条件

特点：

- 规则明确
- 可直接断言
- 很适合进入 DeepEval regression 或普通 pytest

例如：

- 缺少起点时必须提示缺起点
- fallback 不应出现
- recommendations 至少有一项

---

## 6.2 范围接受型

适用于：

- 推荐类输出
- 澄清类输出
- 允许不同表达方式但行为边界明确的场景

特点：

- 不要求精确匹配话术
- 只要求满足一组约束
- 适合 LLM-as-judge 与规则混合判定

例如：

- 推荐内容应与“烧烤、人均 50 内、附近”相关
- 澄清问题应围绕“出去吃还是在家做”

---

## 6.3 对比趋势型

适用于：

- 大批量 experiment
- 看整体指标是否改善
- 分析不同版本 graph / prompt / tool 策略

特点：

- 单条样本不一定做强通过/失败断言
- 更关注整体趋势
- 更适合 Phoenix

例如：

- 某一批地名歧义样本中，fallback rate 是否下降
- 某类复杂约束查询中，工具误调用率是否下降

---

## 7. Evaluator 分层设计

建议将 evaluator 明确拆成四层，每层解决不同问题。

---

## 7.1 结构型 Evaluator

### 目标

验证输出结构是否满足最基本契约。

### 关注点

- 最终输出是否可解析
- `recommendations` / `followups` / `warnings` 是否存在且类型正确
- recommendation item 是否符合 schema

### 适用场景

- 所有样本都适用
- 尤其适合 CI 守门

### 对当前项目的意义

当前输出结构已经由 [app/agent/schemas.py](app/agent/schemas.py#L7-L45) 明确建模，因此结构型 evaluator 可以成为最底层硬门槛。

---

## 7.2 规则型 Evaluator

### 目标

验证一组确定性业务规则是否被满足。

### 关注点

- 是否非 fallback
- 是否调用了必需工具
- 是否禁止调用某些工具
- 是否包含关键字段或关键业务条件
- 是否进入了应有恢复路径

### 适用场景

- regression case
- 高风险恢复路径
- 关键业务 guardrail

### 对当前项目的意义

当前已有很多可确定规则，例如：

- 空结果后的恢复上下文
- 路线缺参数时的硬提示
- 冰箱为空时的 best-effort 策略

这些都可以从 [app/tests/test_smart_eats_tool_result_handler.py](app/tests/test_smart_eats_tool_result_handler.py#L14-L186) 的现有断言经验中提炼出来。

---

## 7.3 LLM Judge 型 Evaluator

### 目标

评估那些无法靠简单规则覆盖的质量维度。

### 关注点

- 回答相关性
- 推荐是否可执行
- 澄清是否合理
- 解释是否自然
- 路线总结是否抓住重点

### 适用场景

- Golden dataset
- 推荐类与自然语言质量评测
- Phoenix experiment 中的批量离线对比

### 使用原则

- 只在规则不够时使用
- 尽量搭配明确 rubric
- 不要让 LLM Judge 成为唯一真相来源

---

## 7.4 轨迹型 Evaluator

### 目标

利用 trace 或 tool 调用记录，评估 agent 过程是否合理。

### 关注点

- 工具调用序列是否合理
- 是否调用了不该调用的工具
- 是否在错误状态下及时恢复
- 是否出现重复、循环、过早停止

### 适用场景

- Phoenix trace 分析
- 工具编排优化
- graph 改动前后对比

### 对当前项目的意义

Smart-Eats 的价值不仅在“答得像样”，也在“查、找、算、规划”这些过程是否顺畅，因此轨迹型 evaluator 是 Phoenix 体系中的关键部分。

---

## 8. Evaluator 分类与执行位置映射

为了避免 evaluator 定义出来却不知道放哪跑，建议明确映射关系。

| Evaluator 类型 | 主要用途 | 主要执行位置 |
|---|---|---|
| 结构型 | 保底契约检查 | DeepEval / pytest |
| 规则型 | 关键业务断言 | DeepEval / pytest，也可在 Phoenix 复用 |
| LLM Judge 型 | 语言质量与业务可执行性评估 | Phoenix experiment 为主 |
| 轨迹型 | tool / graph / 恢复路径合理性 | Phoenix 为主 |

设计原则是：

- **越确定、越稳定的 evaluator，越适合进 CI**
- **越依赖上下文与整体轨迹的 evaluator，越适合留在 Phoenix**

---

## 9. Smart-Eats 业务场景下的 evaluator 设计建议

下面按业务场景给出更贴近项目的 evaluator 设计建议。

---

## 9.1 外出吃饭（eat_out）

### 结果层关注点

- 是否真的给出餐厅或外出吃饭方向建议
- 是否考虑了用户地点、预算、品类
- 是否避免无意义 fallback

### 过程层关注点

- 缺少位置时是否先解决位置问题
- 搜索空结果时是否走恢复策略
- 是否误走做饭或闲聊逻辑

### 建议 evaluator

- 非 fallback 规则检查
- recommendation type 应包含 `restaurant`
- 预算/口味约束命中检查
- 位置恢复路径检查
- 餐厅推荐可执行性 LLM Judge

---

## 9.2 在家做饭（cook_home）

### 结果层关注点

- 是否给出 recipe 类型建议
- 是否利用已有冰箱食材
- 是否给出可操作 followup

### 过程层关注点

- 是否合理使用 `get_fridge_items`
- 冰箱为空时是否走 best-effort 策略
- 是否误转到外出吃饭

### 建议 evaluator

- recommendation type 应包含 `recipe`
- 是否提及库存食材
- 冰箱为空时是否避免硬失败
- 做菜建议可执行性 LLM Judge

---

## 9.3 路线规划（route）

### 结果层关注点

- 是否给出清晰路线结论
- 是否包含关键路程信息
- 缺参数时是否给出明确缺失提示

### 过程层关注点

- 是否正确使用 `plan_route`
- 缺起点/终点时是否走硬 guardrail
- 成功后是否尽快收束到 final answer

### 建议 evaluator

- 必须调用 `plan_route` 的规则检查
- 缺参提示检查
- 输出中是否包含距离/时间/步骤摘要
- “成功后不再乱调用其他工具”的轨迹检查

---

## 9.4 模糊意图 / 澄清场景（unknown / clarify）

### 结果层关注点

- 是否合理澄清而不是敷衍 fallback
- 澄清问题是否收敛到关键分歧点

### 过程层关注点

- 是否在低置信度下保持保守
- 是否避免误调用大量无关工具

### 建议 evaluator

- need_clarify 期望检查
- 澄清问题相关性 Judge
- 禁止无必要工具调用检查

---

## 10. 首批样本字段建议

从设计角度，建议第一版字段不要过重，但要能覆盖当前真实问题。

可以将单条样本抽象为如下逻辑结构：

### 10.1 基础信息

- 样本 ID
- 标题
- 数据集层级
- 优先级

### 10.2 输入信息

- 用户 message
- 可选 history
- 可选 agent_type

### 10.3 上下文信息

- 位置
- 冰箱库存
- 用户偏好
- 测试所需外部前置条件

### 10.4 期望信息

- 期望意图集合
- 期望是否澄清
- 期望工具行为
- 期望输出结构
- 期望恢复路径

### 10.5 标签信息

- scene
- capability
- risk
- source

### 10.6 维护信息

- 来源说明
- 创建原因
- 备注

第一版字段设计不追求“永远完美”，但要保证未来新增字段不需要推翻旧样本。

---

## 11. 首批 Regression Case 建议

基于当前 [app/tests/fixtures/replay_cases.json](app/tests/fixtures/replay_cases.json#L1-L65) 和已有恢复逻辑测试，我建议第一批回归集优先从下面几类选。

---

## 11.1 位置即目标场景

样本代表：

- `eatout-location-as-target`

### 为什么重要

这类场景很容易把“用户当前位置”和“目标搜索地点”混淆，导致推荐范围偏离。

### 应重点检查

- 不应 fallback
- 不应误判到 cook_home
- 应给出可执行的附近餐厅建议

---

## 11.2 空结果恢复场景

样本代表：

- `eatout-empty-result-recovery`

### 为什么重要

空结果是高频真实问题，若恢复路径不稳，用户体验会很差。

### 应重点检查

- 不应第一时间 fallback
- 应存在恢复路径或 best-effort 行为
- 不应直接终止在空结果状态

---

## 11.3 路线 follow-up 场景

样本代表：

- `route-followup`

### 为什么重要

路线规划往往对工具链依赖最强，也最容易暴露“工具成功后没有正确收束”的问题。

### 应重点检查

- 应识别为 route 或至少不偏离严重
- 应调用 `plan_route`
- 成功后应给出路线结论，不应继续无意义调用工具

---

## 11.4 外出吃饭约束场景

样本代表：

- `eatout-cuisine-budget`
- `eatout-night-snack`

### 为什么重要

这是 Smart-Eats 的核心价值场景，决定推荐是否贴近实际需求。

### 应重点检查

- 预算约束是否被考虑
- 品类约束是否被考虑
- 夜宵类 query 是否被当作一般闲聊处理

---

## 11.5 在家做饭场景

样本代表：

- `cook-home-query`

### 为什么重要

这是与 eat_out 平行的重要主场景，不能只把主要精力放在餐厅推荐上。

### 应重点检查

- 应偏向 recipe 输出
- 应与现有食材相关
- 不应误转为餐厅推荐

---

## 11.6 澄清与轻量闲聊场景

样本代表：

- `clarify-scene`
- `chat-greeting`

### 为什么重要

如果 agent 无法处理“低信息量”输入，就会频繁出现误工具调用和体验割裂。

### 应重点检查

- 模糊问题是否优先澄清
- 纯闲聊是否不需要重型工具链
- 不应因为信息少而直接 fallback

---

## 12. 样本进入 DeepEval 的筛选标准

不是所有 dataset 都应该进 CI。

建议样本只有同时满足以下条件时，才进入 DeepEval regression suite：

### 12.1 判定稳定

同一 case 多次运行，结果波动较小。

### 12.2 风险高

一旦失败，对用户体验或核心能力影响明显。

### 12.3 规则明确

至少有一部分条件可以用规则稳定判断，而不是完全依赖主观评估。

### 12.4 执行成本可接受

不会让 CI 因样本过多或流程过重而失控。

这意味着：

- Golden dataset 不等于全部进 CI
- Stress dataset 通常不进 CI
- Regression dataset 也要二次筛选后再进 CI

---

## 13. 样本进入 Phoenix Experiment 的筛选标准

相比 DeepEval，Phoenix 更适合收纳更宽的样本池。

建议满足以下任一条件的样本优先进入 Phoenix：

- 当前规则难以稳定判定，但值得观察趋势
- 需要看 tool/trace 才能解释问题
- 想比较两个 prompt / graph 策略
- 想分析某类长尾失败
- 想评估复杂多轮或复杂约束场景

Phoenix 的重点不是“硬拦截”，而是“帮助理解变化”。

---

## 14. Evaluator 运营建议

设计 evaluator 只是开始，长期维护更重要。

---

## 14.1 先少量高质量，再逐步扩展

第一批 evaluator 不宜过多。

建议先有：

- 结构型 1 组
- 规则型 3~5 组
- LLM Judge 型 1~2 组
- 轨迹型 1~2 组

先把口径打稳，再扩充。

---

## 14.2 每个 evaluator 都要有清晰适用范围

不要让 evaluator 变成“什么都想评”的万能函数。

每个 evaluator 最好都能明确回答：

- 它评哪类样本
- 它依赖哪些输入字段
- 它输出什么分数或结论
- 它适合在 Phoenix 还是 DeepEval 中运行

---

## 14.3 业务 evaluator 优先从历史问题中长出来

比起凭空发明复杂 evaluator，更好的方式是：

- 先看历史 bad case
- 再抽象出共性失败模式
- 最后把失败模式写成 evaluator

这样 evaluator 会更贴合真实痛点，而不是看起来高级但不解决问题。

---

## 14.4 不要过度依赖单一总分

对于 Agent 系统，单一总分很容易掩盖问题。

例如一个样本可能：

- 回答相关性高
- 但工具调用完全错误
- 或者最终给出了推荐，但预算约束没满足

因此建议长期保留“分维度评测结果”，而不是只看一个 aggregate score。

---

## 15. 最终建议

结合当前仓库现状，我建议第三份文档的落地方向是：

### 数据集方面

- 先把现有 replay cases 升级为正式 regression seeds
- 同步建立 golden / regression / stress 三层样本语义
- 样本字段以“输入、上下文、期望、标签、来源”五大块为核心

### Evaluator 方面

- 先建立结构型与规则型 evaluator 作为底层硬门槛
- 再引入少量 LLM Judge 处理推荐质量与自然语言质量
- 最后补轨迹型 evaluator，用于 Phoenix 分析 tool / graph 行为

### 工程策略方面

- DeepEval 只收最关键、最稳定、最值得拦截的 regression cases
- Phoenix 收更宽的样本池，用于 experiment 和趋势分析
- 让 evaluator 先成为可复用规则，再分别挂到 Phoenix 与 DeepEval 上

如果按这个方向推进，Smart-Eats 的评测体系会从“有一些 replay 和日志指标”，逐步升级为：

- 有正式 dataset
- 有清晰标签体系
- 有分层 evaluator
- 有 Phoenix experiment
- 有 DeepEval regression gate

这意味着后续无论是调 prompt、改 graph、增 tool，还是修 recovery 路径，都会有更明确的质量反馈机制。

---

## 16. 下一步建议

在本文档之后，最适合补的下一份文档是：

**《实施计划与接入步骤》**

重点可以写成：

1. Phoenix tracing 首批接入哪些点
2. `evals/datasets/` 第一版目录怎么建
3. 首批 evaluator 先实现哪几个
4. DeepEval 回归集先接哪 10~20 条 case
5. 本地运行与 CI 运行的分层策略

这样就能从“设计文档阶段”进入“具体执行阶段”。

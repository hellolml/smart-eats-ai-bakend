> ⚠️ **已过期** — 本文档设计的是 Phoenix + DeepEval 时期的理想目录结构，与当前 `evals/` 实际结构不符（扁平 evaluator、fixture/live 双模式、PostgreSQL 持久化、Web 工作台等均未按此文档实施）。仅供参考，请以 `evaluation-usage-guide.md` 和实际代码为准。

# Smart-Eats Agent 评测体系目录与模块落地设计

## 1. 文档目标

本文档是 [phoenix-deepeval-evaluation-architecture.md](docs/phoenix-deepeval-evaluation-architecture.md) 的配套落地文档。

第一份文档回答的是：

- 为什么要采用 Phoenix + DeepEval
- 评测体系的职责分层是什么
- 样本、实验、回归之间如何协作

本文档回答的是：

- 这套评测体系在当前仓库里应该如何组织目录
- Phoenix tracing 应该挂在哪些模块
- dataset / evaluator / experiment / regression test 应该放在哪里
- 现有 replay、metrics、pytest 资产如何平滑并入新体系

本文档尽量避免实现细节，重点给出**目录设计、模块职责、演进路径与迁移原则**。

---

## 2. 当前仓库现状

当前项目和 agent 评测最相关的目录主要有：

### 2.1 应用运行主链路

- 应用入口： [app/main.py](app/main.py)
- 对话 API： [app/api/v1/chat.py](app/api/v1/chat.py)
- Agent 编排： [app/agent/graph.py](app/agent/graph.py)
- 业务 Agent： [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py)
- 工具实现： [app/agent/tools/](app/agent/tools/)
- 指标记录： [app/agent/metrics.py](app/agent/metrics.py)

### 2.2 现有评测与分析资产

- 指标说明文档： [docs/agent-metrics.md](docs/agent-metrics.md)
- replay 脚本： [scripts/replay_eval.py](scripts/replay_eval.py)
- dashboard 合并脚本： [scripts/agent_eval_dashboard.py](scripts/agent_eval_dashboard.py)
- 指标汇总脚本： [scripts/agent_metrics_summary.py](scripts/agent_metrics_summary.py)
- replay fixtures： [app/tests/fixtures/replay_cases.json](app/tests/fixtures/replay_cases.json)

### 2.3 现有测试体系

- 测试目录： [app/tests/](app/tests/)
- replay 相关测试： [app/tests/test_replay_eval.py](app/tests/test_replay_eval.py)
- dashboard 脚本测试： [app/tests/test_agent_eval_dashboard.py](app/tests/test_agent_eval_dashboard.py)
- metrics 汇总测试： [app/tests/test_agent_metrics_summary.py](app/tests/test_agent_metrics_summary.py)

### 2.4 现状特点

当前状态的优点是：

- 已有 API / agent / tool 的明确边界
- 已有日志指标
- 已有 replay 思路
- 已有 pytest 基础

但问题也很明显：

- 评测资产散落在 `scripts/`、`docs/`、`app/tests/fixtures/` 中
- replay case 还是测试夹具语义，不是正式 dataset 语义
- 没有独立的 evaluator 目录
- 没有 experiment 入口层
- tracing、dataset、回归测试之间尚未形成统一结构

因此，第二份文档的目标不是“推翻重来”，而是：

**在保留现有资产的前提下，把评测体系收拢到一套清晰目录中。**

---

## 3. 设计原则

## 3.1 运行代码与评测资产分离

应用运行代码继续保留在 [app/](app/) 下。

评测相关资产应逐步独立到单独空间，避免出现：

- 数据集混在测试夹具里
- 实验脚本混在通用脚本里
- 业务 evaluator 混在 agent 主逻辑里

核心原则是：

- `app/` 负责运行
- `evals/` 负责评测资产与实验
- `tests/agent_eval/` 负责回归门禁
- `scripts/` 只保留通用或兼容性入口

---

## 3.2 先兼容现状，再逐步收敛

当前项目里已经有 replay 和 metrics 相关脚本，不建议立即做激进搬迁。

更稳妥的方式是：

1. 先引入目标目录结构
2. 再让新能力优先落在新目录里
3. 最后把旧脚本逐步转成 wrapper 或迁移入口

这样可以避免：

- 历史测试大面积改动
- 文档与脚本路径瞬间失效
- 开发者短期找不到入口

---

## 3.3 把“样本”“评测器”“实验”“回归测试”拆开

这四类资产职责不同，必须分目录管理。

### 样本（dataset）
回答“评什么”。

### 评测器（evaluator）
回答“怎么判”。

### 实验（experiment）
回答“怎么批量比较版本”。

### 回归测试（regression test）
回答“哪些结果必须守住”。

如果把它们混在一起，后期维护会非常痛苦。

---

## 4. 目标目录结构

建议分两层看：

- **近期可落地目录**：尽量少动现有结构
- **中期目标目录**：形成完整评测体系

---

## 4.1 近期可落地目录

在不大动 `app/tests/` 的前提下，先引入 `evals/`：

```text
app/
  agent/
  api/
  tests/
    fixtures/
      replay_cases.json
    ...

docs/
  phoenix-deepeval-evaluation-architecture.md
  evaluation-directory-and-module-design.md

scripts/
  replay_eval.py
  agent_eval_dashboard.py
  agent_metrics_summary.py

evals/
  datasets/
    golden/
    regression/
    stress/
  evaluators/
    business/
    trajectory/
    safety/
  experiments/
    phoenix/
  reports/
```

这一阶段的重点不是立刻迁完，而是先建立评测资产的“正式归属地”。

---

## 4.2 中期目标目录

当 Phoenix 与 DeepEval 正式接入后，建议收敛到如下结构：

```text
app/
  agent/
    agents/
    tools/
    metrics.py
    tracing.py
  api/
  main.py

docs/
  phoenix-deepeval-evaluation-architecture.md
  evaluation-directory-and-module-design.md
  evaluation-dataset-and-evaluator-design.md
  evaluation-implementation-plan.md

evals/
  datasets/
    golden/
    regression/
    stress/
  evaluators/
    business/
    trajectory/
    safety/
    shared/
  experiments/
    phoenix/
  adapters/
  reports/
  configs/

tests/
  agent_eval/
    regression/
    smoke/
    fixtures/

scripts/
  eval_replay.py
  eval_dashboard.py
  eval_metrics_summary.py
```

这里的核心变化有两点：

1. `app/agent/` 中引入专门的 tracing 接入模块
2. `tests/agent_eval/` 从 `app/tests/` 独立出来，专门承载 agent 评测回归测试

---

## 5. 各目录职责设计

## 5.1 app/agent/

这是应用运行主链路，不应该变成“评测逻辑大杂烩”。

### 5.1.1 保留职责

- 编排 agent 执行
- 调用模型与工具
- 输出最终响应
- 记录最基础的运行指标

### 5.1.2 新增职责边界

未来评测体系接入后，这里只新增：

- tracing 接入点
- 少量统一埋点适配
- 与 evaluator/experiment 无关的通用运行元数据

这里**不应该**直接承载：

- 离线 experiment 逻辑
- dataset 管理逻辑
- 回归测试逻辑

### 5.1.3 建议新增模块

建议未来在 [app/agent/](app/agent/) 下增加一个轻量模块，例如：

- `tracing.py`

它的职责是：

- 统一封装 Phoenix/OpenInference 初始化
- 统一封装 trace/span 生成方式
- 减少在 [app/agent/graph.py](app/agent/graph.py) 与工具层四处散落 tracing 细节

这能避免运行主链路代码被可观测性接入污染得太重。

---

## 5.2 evals/datasets/

这是未来评测体系最重要的目录之一。

### 5.2.1 目录职责

集中存放所有正式评测样本，不再把评测样本仅仅视作测试夹具。

### 5.2.2 分层设计

建议分三层：

- `golden/`：稳定核心样本
- `regression/`：历史问题样本
- `stress/`：复杂与边界样本

### 5.2.3 为什么不继续放 app/tests/fixtures/

`fixtures/` 的语义更像“给测试喂数据”，而不是“长期运营的评测资产”。

当样本变多、要打标签、要切片实验、要多套 evaluator 复用时，`fixtures/` 语义就太弱了。

### 5.2.4 现有资产迁移策略

当前 [app/tests/fixtures/replay_cases.json](app/tests/fixtures/replay_cases.json) 建议作为第一批迁移对象。

建议路径演进：

- 第一阶段：保留原文件，新增对应 dataset 目录
- 第二阶段：让 replay 脚本优先支持从 `evals/datasets/...` 读取
- 第三阶段：将旧路径保留为兼容别名，最终再清理

这样不会打断现有 [scripts/replay_eval.py](scripts/replay_eval.py) 与 [app/tests/test_replay_eval.py](app/tests/test_replay_eval.py) 的使用习惯。

---

## 5.3 evals/evaluators/

这是“如何判分”的集中定义目录。

### 5.3.1 为什么必须独立

如果 evaluator 逻辑分散在：

- 测试文件里
- Phoenix experiment 脚本里
- agent 主代码里

后面一定会出现重复定义、口径不一致和难以复用的问题。

### 5.3.2 目录划分建议

建议按语义拆为：

- `business/`：业务效果判定
- `trajectory/`：轨迹与工具链路判定
- `safety/`：安全与拒答判定
- `shared/`：公共工具、评分结构、标签映射

### 5.3.3 设计原则

evaluator 应尽量做到：

- 与 dataset 解耦
- 与具体 experiment 解耦
- 与 DeepEval / Phoenix 执行器解耦

也就是 evaluator 本身先是“判分规则”，然后再由 Phoenix experiment 或 DeepEval test 去调用。

这样未来能最大化复用。

---

## 5.4 evals/experiments/phoenix/

这是 Phoenix 离线实验的正式入口层。

### 5.4.1 目录职责

负责：

- 装配 dataset
- 装配 evaluator
- 选择被评测的 agent target
- 启动 experiment
- 输出结果与报告

### 5.4.2 为什么不直接放 scripts/

`scripts/` 更适合：

- 一次性辅助脚本
- 通用工具脚本
- 兼容性命令入口

但 Phoenix experiment 是未来评测体系的正式组成部分，不应只是一个临时脚本。

### 5.4.3 与 scripts/ 的关系

推荐做法是：

- 真正的 experiment 装配逻辑放在 `evals/experiments/phoenix/`
- `scripts/` 里只保留薄入口，方便命令行调用

也就是：

- `evals/experiments/phoenix/` 是“内部实现层”
- `scripts/` 是“命令入口层”

---

## 5.5 evals/adapters/

这是中期非常有价值、但容易被忽略的一层。

### 5.5.1 目录职责

集中处理：

- 当前 agent 请求格式与 dataset 样本格式之间的映射
- 当前 agent 输出结构与 evaluator 输入结构之间的映射
- trace 数据与业务报告之间的转换

### 5.5.2 为什么需要这一层

如果没有 adapter 层，往往会出现两种坏味道：

1. dataset 为了迁就代码，字段设计越来越丑
2. test/experiment 为了迁就 agent 接口，到处写转换逻辑

adapter 层存在的意义，就是把“评测语义”和“应用实现细节”隔开。

### 5.5.3 在当前项目里的价值

当前 [scripts/replay_eval.py](scripts/replay_eval.py) 直接以 `/api/v1/chat/sessions/{session_id}/stream` 为驱动，这种方式适合现在，但长期会导致：

- 评测用例过度绑定接口形态
- evaluator 难以复用到非 HTTP 场景

因此未来建议把：

- API 调用适配
- session 创建逻辑
- SSE final 结果提取逻辑

都逐步沉淀到 adapter 层中。

---

## 5.6 evals/reports/

这是实验与评测产出的收敛目录。

### 5.6.1 目录职责

存放：

- 本地 experiment 输出
- 临时对比报告
- 导出的聚合结果
- 非源码评测产物

### 5.6.2 为什么需要单独目录

如果把所有结果都散在项目根目录，后面会很快失控。

当前 [scripts/replay_eval.py](scripts/replay_eval.py) 默认输出 `replay_report.json`，未来建议逐步收口到 `evals/reports/`，避免根目录堆积临时文件。

---

## 5.7 evals/configs/

这层可以稍后再加，但中期很有用。

### 5.7.1 目录职责

存放：

- experiment 运行配置
- dataset 选择配置
- evaluator 组合配置
- 不同环境下的评测参数

### 5.7.2 作用

让评测体系从“写死在脚本里”逐步变成“可配置资产”。

对长期维护尤其重要。

---

## 5.8 tests/agent_eval/

这是 DeepEval 回归门禁的正式目录。

### 5.8.1 为什么建议从 app/tests/ 独立出来

[app/tests/](app/tests/) 当前更像“应用测试总目录”，其中混有：

- API 测试
- 模块测试
- 指标脚本测试
- 部分评测相关测试

如果未来继续把所有 agent 评测也塞进去，会越来越难区分：

- 传统 correctness test
- agent evaluation test
- experiment support test

把 agent 评测单独拆出，可以更清晰表达：

**这是质量门禁，而不是普通功能测试。**

### 5.8.2 子目录建议

建议拆为：

- `regression/`：关键回归集
- `smoke/`：轻量冒烟验证
- `fixtures/`：仅服务于测试执行的小型夹具

### 5.8.3 与 evals/datasets/ 的关系

- `evals/datasets/` 存长期运营的正式样本
- `tests/agent_eval/fixtures/` 只存测试过程需要的小型局部数据

不要反过来用 `tests/.../fixtures/` 充当正式 dataset。

---

## 5.9 scripts/

`scripts/` 未来仍然保留，但职责要收窄。

### 5.9.1 保留职责

- 命令行入口
- 本地辅助脚本
- 开发者便捷工具
- 兼容旧用法的 wrapper

### 5.9.2 不再承载的职责

不建议继续把核心评测设计都堆在这里，例如：

- evaluator 主定义
- experiment 主装配
- dataset 主配置

这些都应该回到 `evals/` 中。

### 5.9.3 对当前脚本的建议

#### [scripts/replay_eval.py](scripts/replay_eval.py)

建议中期演化为：

- 作为 replay / local eval 的 CLI 入口
- 内部调用 `evals/adapters/` 和 `evals/datasets/`
- 不再自己持有全部评测逻辑

#### [scripts/agent_eval_dashboard.py](scripts/agent_eval_dashboard.py)

建议中期演化为：

- 作为报告聚合命令入口
- 底层聚合逻辑逐步迁到 `evals/reports/` 或 `evals/shared/` 支持模块

#### [scripts/agent_metrics_summary.py](scripts/agent_metrics_summary.py)

建议保留，继续承担：

- 日志指标快速离线汇总
- 线上观测补充视角

它与 Phoenix 不冲突，而是补充关系。

---

## 6. Phoenix tracing 模块挂载点

目录设计只是外层，真正能跑起来还要明确 tracing 接入位置。

---

## 6.1 API 入口层

建议在 [app/api/v1/chat.py](app/api/v1/chat.py) 建立 request 级 trace 起点。

这里适合挂的内容包括：

- 请求唯一标识
- session id
- 用户输入摘要
- agent_type
- 请求生命周期起止时间

这一层的作用是：

**把一次用户请求定义成一条完整评测链路的根节点。**

---

## 6.2 Graph 编排层

[app/agent/graph.py](app/agent/graph.py) 是最关键的 tracing 接入点。

这里应重点记录：

- 节点进入与退出
- planner / writer 过程
- context 构建
- 工具调用决策
- fallback / clarify / early stop 等关键状态分支

从评测角度看，这一层是 Phoenix 能否真正解释 agent 行为的关键。

---

## 6.3 业务 Agent 层

[app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py) 更适合记录业务语义级事件，例如：

- fast path 是否命中
- 当前场景识别结果
- 业务规则分支
- prompt 构建策略选择

Graph 层看的是流程，业务 Agent 层看的是业务意图，两者不能互相替代。

---

## 6.4 Tool 层

[app/agent/tools/](app/agent/tools/) 适合记录：

- 工具名称
- 输入参数摘要
- 命中缓存与否
- 结果条数或结果摘要
- 错误类型
- 耗时

注意这里建议记录“摘要”，而不是盲目记录全部原始 payload，避免 trace 过重。

---

## 6.5 Metrics 层

[app/agent/metrics.py](app/agent/metrics.py) 当前承担的是轻量计数与日志输出。

未来它仍然有价值，但建议定位为：

- 轻量运行指标记录
- Phoenix 之外的补充监控接口
- 内部 metrics API 的底层支持

而不是试图扩展成完整 experiment 平台。

---

## 7. 现有资产的迁移设计

## 7.1 replay_cases.json 的迁移

当前文件： [app/tests/fixtures/replay_cases.json](app/tests/fixtures/replay_cases.json)

建议迁移方式：

### 阶段一

- 保留原文件
- 在 `evals/datasets/regression/` 中新增正式版本
- 让新文档与新脚本优先引用 `evals/datasets/`

### 阶段二

- `scripts/replay_eval.py` 支持新路径作为默认路径
- 旧路径保留显式参数兼容

### 阶段三

- `app/tests/fixtures/replay_cases.json` 降级为兼容拷贝或删除

这样迁移成本最低。

---

## 7.2 replay_eval.py 的迁移

当前 [scripts/replay_eval.py](scripts/replay_eval.py) 同时承担了：

- 读取样本
- 调接口
- 解析 SSE
- 形成报告

长期来看职责偏重。

建议演进为三层：

1. `evals/adapters/` 负责调用适配与结果提取
2. `evals/datasets/` 提供样本
3. `scripts/replay_eval.py` 只做 CLI 入口与参数组织

这样后续无论：

- 本地脚本跑
- Phoenix experiment 跑
- DeepEval regression 跑

都能复用同一套适配逻辑。

---

## 7.3 agent_eval_dashboard.py 的迁移

当前 [scripts/agent_eval_dashboard.py](scripts/agent_eval_dashboard.py) 是一个很好的“聚合思路雏形”。

建议未来把它的角色明确为：

- 衔接现有 metrics / replay 与未来 Phoenix 结果的桥接层
- 作为统一 scorecard 的命令入口

它不一定要消失，但可以从“独立脚本”演进为“统一报告层的入口”。

---

## 7.4 app/tests 中评测相关测试的迁移

当前这些测试：

- [app/tests/test_replay_eval.py](app/tests/test_replay_eval.py)
- [app/tests/test_agent_eval_dashboard.py](app/tests/test_agent_eval_dashboard.py)
- [app/tests/test_agent_metrics_summary.py](app/tests/test_agent_metrics_summary.py)

建议短期保留原位。

中期可考虑：

- 与普通 API/模块测试继续同处 `app/tests/`
- 或把 agent 评测相关测试拆到 `tests/agent_eval/`

我的建议是：

- **工具脚本正确性测试** 留在 `app/tests/`
- **DeepEval 回归门禁测试** 放到 `tests/agent_eval/`

这样边界最清晰。

---

## 8. 推荐的模块边界

为了避免未来实现时越写越乱，建议尽早约束模块边界。

---

## 8.1 app/ 与 evals/ 的边界

### app/

负责：

- 业务运行
- trace 埋点
- 指标记录
- 对外 API

### evals/

负责：

- 样本定义
- evaluator 定义
- 实验装配
- 适配层
- 报告产出

边界原则：

**app 不依赖 experiment，experiment 通过 adapter 调用 app。**

---

## 8.2 evals/ 与 tests/ 的边界

### evals/

负责长期资产。

### tests/

负责测试执行与门禁。

边界原则：

- `evals/` 是“评测内容库”
- `tests/` 是“测试运行层”

这意味着测试可以引用 `evals/`，但不建议反过来让 dataset 长在 `tests/` 里。

---

## 8.3 scripts/ 与 evals/ 的边界

### scripts/

面向开发者命令入口。

### evals/

面向内部实现与复用。

边界原则：

**脚本薄、实现厚。**

也就是：

- 脚本尽量只解析参数和调用模块
- 复杂逻辑回到 `evals/`

---

## 9. 环境与配置落地建议

目录设计之外，还要避免配置散落。

建议未来把评测相关配置分成三类：

### 9.1 运行时 tracing 配置

挂在应用环境配置中，用于控制：

- tracing 是否开启
- Phoenix endpoint / project 配置
- 本地开发与测试环境差异

### 9.2 experiment 配置

放在 `evals/configs/` 中，用于控制：

- 使用哪个 dataset
- 使用哪些 evaluator
- 比较哪个版本 target

### 9.3 regression 配置

放在测试层或 pytest 配置中，用于控制：

- 跑哪一组 regression suite
- 本地快速模式与完整模式差异
- CI 阈值策略

配置分层的意义在于：

- tracing 是运行时配置
- experiment 是评测资产配置
- regression 是测试执行配置

三者不要混成一套。

---

## 10. 推荐的实施顺序

目录设计不能只停留在纸面上，建议按以下顺序落地。

---

## 10.1 第一步：引入 evals/ 目录

先创建：

- `evals/datasets/`
- `evals/evaluators/`
- `evals/experiments/phoenix/`
- `evals/reports/`

这一步即使还没正式接 Phoenix，也值得先做，因为它能立刻让评测资产有统一归属。

---

## 10.2 第二步：把 replay 资产视为正式 dataset 种子

把 [app/tests/fixtures/replay_cases.json](app/tests/fixtures/replay_cases.json) 复制或迁移为 `evals/datasets/regression/` 中的初版样本。

这一步的意义是：

- 现有 replay 不再只是测试夹具
- 它成为正式评测资产的起点

---

## 10.3 第三步：在 app/agent/ 增加 tracing 模块

在 [app/agent/](app/agent/) 中补一个统一 tracing 模块，先把 Phoenix 接入点标准化。

这一步完成后，Phoenix 才真正有稳定挂载点。

---

## 10.4 第四步：抽 adapter 层

把当前 replay 和未来 experiment / regression 都需要的：

- 请求构造
- session 创建
- SSE final 提取
- 输出标准化

逐步从脚本里抽离到 `evals/adapters/`。

这是未来可复用性的关键一步。

---

## 10.5 第五步：独立 tests/agent_eval/

当第一批 DeepEval 回归集要正式接入时，再引入 `tests/agent_eval/`。

这样不会过早把目录拆得太复杂，也不会等到后面已经混乱了再补救。

---

## 11. 最终建议

结合当前仓库现状，我建议采用“**保守迁移、分层收敛**”策略：

### 近期

- 新增 `evals/` 目录
- 保留 `app/tests/` 与 `scripts/` 现状
- 先把 replay case 视作正式 dataset 种子

### 中期

- 在 [app/agent/](app/agent/) 增加 tracing 模块
- 在 `evals/` 中形成 dataset / evaluator / experiment / adapter 四层
- 将 DeepEval 回归测试拆到 `tests/agent_eval/`

### 长期

- `scripts/` 只保留 CLI 壳
- `evals/` 成为统一评测资产中心
- `tests/agent_eval/` 成为稳定质量门禁入口

如果按这个路线推进，当前仓库会形成一个比较清晰的结构：

- [app/](app/) 负责运行
- `evals/` 负责评测资产与实验
- `tests/agent_eval/` 负责回归守门
- [scripts/](scripts/) 负责命令入口
- [docs/](docs/) 负责方案与实施说明

这套结构足够支撑 Phoenix + DeepEval 的中长期演进，而且不会一上来就对现有项目造成过大扰动。

---

## 12. 下一份文档建议

在本文档之后，最适合继续补的是：

**《数据集与 Evaluator 设计》**

重点写清楚：

- dataset 样本字段怎么定
- 标签体系怎么定
- 哪些属于业务 evaluator
- 哪些属于轨迹 evaluator
- 第一批 regression case 如何划分

有了这份文档，评测体系就从“架构和目录设计”进一步进入“评什么、怎么判”的可实施阶段。

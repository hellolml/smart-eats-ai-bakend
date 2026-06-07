# Smart-Eats-AI 评测系统说明与面试问答

本文用于系统性说明 Smart-Eats-AI 当前评测系统的设计、实现、使用方式和可讨论的工程取舍。它面向两类读者：

- 项目维护者：快速理解评测链路、数据流、自动化和排障方式。
- 面试/技术评审场景：能够清楚回答“为什么这么设计、怎么保证稳定、如何扩展、有哪些不足”。

相关操作手册见 [evaluation-usage-guide.md](./evaluation-usage-guide.md)。

## 1. 一句话概述

Smart-Eats-AI 的评测系统把 Agent 评测拆成两层：

- **PR 离线确定性门禁**：用 fixture trace 跑 evaluator、scoring、threshold、reporter 和 CI，不依赖真实模型、地图 API、Phoenix、DeepEval。
- **定时/手动真实 live 评测**：调用真实 backend 和 Agent，采集 SSE trace，支持 Phoenix 和可选 LLM Judge，用于发现真实模型、工具、路由、恢复策略和业务质量问题。

产物同时写入：

- **JSON**：作为 CI threshold、artifact 和离线审计入口。
- **PostgreSQL**：作为 Web 评测工作台的主数据源，支持运行历史、对比、case 详情和 trace timeline。

## 2. 当前实现程度

当前已经实现：

- `run_eval.py` 统一评测入口。
- `fixture` 和 `live` 两类 runner。
- `quick`、`full`、`live-smoke` 三类 suite。
- 5 个业务 scene：`eat_out`、`cook_home`、`route`、`travel_planner`、`chat`。
- 5 类 case category：`normal`、`boundary`、`tool_failure`、`safety`、`regression`。
- 确定性 evaluator：intent、tool、task、constraint、schema、recovery、efficiency、safety、travel state。
- 可选 LLM Judge evaluator。
- JSON report、HTML report、console report。
- failure summary、failure class、threshold failures、missing metrics、trace timeline。
- P0 单例强门禁、全局阈值、category/scene scoped threshold。
- GitHub Actions PR quick eval、scheduled/manual full eval。
- `/evals.html` 只读 Web 工作台。
- 内部 API：report、report list、compare、case detail。
- PostgreSQL 双写和 DB-first API fallback。
- 历史 JSON 导入 PostgreSQL 脚本。
- 在线监控 conversation 表、异步轻量评测持久化。
- 受保护的 `/api/v1/internal/monitoring/*` API。
- `frontend` 的 `/admin/evaluations` 长期主入口。
- `frontend_new` 的 `eval-workbench` 移动/新版入口。
- 只读 Datasets API 和前端展示。
- Human Review v1 审核记录。

当前还没有实现：

- Web 页面触发评测。
- Dataset Web 编辑。
- 人工 override 分数和 Dataset 编辑。
- 生产 trace replay 到离线 dataset。
- 深度 LLM Judge 对线上 trace 的全量事实性评测。
- 成本模型价格表和真实 token usage 计费接入。
- PostgreSQL retention/分区和 OLAP/Prometheus 级别高频指标。
- PostgreSQL 查询完全下沉到规范化表；离线评测部分能力仍复用 raw report JSON。

## 3. 设计目标

评测系统解决四个问题：

1. **PR 稳定性**
   PR 不能因为模型订阅、地图 API、Phoenix 或 DeepEval 不稳定而随机失败。PR 只验证确定性评测链路。

2. **真实质量可见性**
   Agent 的真实质量仍必须通过 live eval 验证，包括模型输出、工具调用、路由、恢复路径和安全表现。

3. **失败可定位**
   报告不能只说“分数低”，还要能定位到 case、metric、scene、category、tool、worker、failure class 和 trace event。

4. **可长期分析**
   JSON 适合 CI 和 artifact，但 Web 工作台需要持久化运行历史、run 对比和后续人工审阅，所以引入 PostgreSQL。

## 4. 核心架构

整体链路：

```text
EvalCase dataset
  -> EvalHarness
  -> TrialRunner
  -> FixtureRunner or AgentRunner
  -> EvalTrace
  -> Evaluators
  -> EvalReport
  -> Console / JSON / HTML / PostgreSQL
  -> CI threshold / Web API / Web workbench
```

核心模块：

| 模块 | 作用 |
| --- | --- |
| `evals/scripts/run_eval.py` | 命令行入口，负责参数解析、运行评测、输出报告、DB 持久化、阈值检查 |
| `evals/runners/harness.py` | 评测总控，加载 suite、执行 trials、调用 evaluators、生成 EvalReport |
| `evals/runners/trial_runner.py` | 根据 runner 类型选择 fixture 或 live 执行 |
| `evals/adapters/fixture_runner.py` | 从 fixture SSE events 构造 EvalTrace，不访问网络 |
| `evals/adapters/agent_runner.py` | 调真实 backend，封装 SSEAdapter |
| `evals/adapters/sse_adapter.py` | 解析 SSE 事件流，构造 EvalTrace |
| `evals/evaluators/*` | 各维度评分器 |
| `evals/reporters/reporters.py` | Console、JSON、HTML 报告 |
| `evals/scripts/check_thresholds.py` | CI 阈值检查 |
| `evals/persistence/postgres.py` | JSON report 到 DB 的 upsert、查询和对比 |
| `app/api/v1/internal.py` | Web 工作台内部 API |
| `app/static/evals.html` | 评测 Web 工作台 |

## 5. 数据模型

### 5.1 EvalCase

一条 case 描述一个用户任务和期望结果。

关键字段：

- `id`：稳定 case id。
- `category`：normal、boundary、tool_failure、safety、regression。
- `scene`：eat_out、cook_home、route、travel_planner、chat。
- `task`：用户输入。
- `initial_context`：初始上下文，例如用户位置、冰箱食材。
- `expectations`：意图、worker、工具调用、输出结构、恢复路径、安全约束。
- `scoring`：可选的 case 级权重。
- `priority`：P0/P1 等优先级。

### 5.2 EvalTrace

`EvalTrace` 是一次 trial 的执行轨迹，来自 fixture events 或真实 SSE。

关键字段：

- `steps`：事件序列。
- `final_json`：最终回答。
- `expected_scene` / `actual_scene`：期望和实际 scene。
- `actual_worker`：实际 worker。
- `active_skills`：激活技能。
- `allowed_tools`：允许工具。
- `tool_call_names`：工具调用列表。
- `recovery_events`：恢复路径事件。
- `error` / `error_reason`：执行错误。
- `phoenix_trace_url`：可选 Phoenix 引用。
- `judge_scores` / `judge_reasons`：可选 LLM Judge 结果。

### 5.3 EvalReport

`EvalReport` 是一次评测运行的汇总。

包含：

- 总 case 数、trial 数、整体成功率。
- category breakdown。
- scene breakdown。
- failure summary。
- 每个 case 的 success rate 和 avg scores。
- 每个 trial 的 scores、weighted score、trace、错误、工具、阈值失败等。

## 6. Suite 与 Runner

### 6.1 Suite

| Suite | 当前数量 | 数据源 | 用途 |
| --- | ---: | --- | --- |
| `quick` | 7 | `fixture_cases.json` | PR 离线门禁 |
| `full` | 50 | `full_cases.json` + `golden_cases.jsonl` | 定时/手动完整真实评测 |
| `live-smoke` | 5 | 从 full/golden 挑代表 case | 快速验证真实 backend/model/API |

`quick` 覆盖：

- scene：eat_out、cook_home、route、travel_planner、chat。
- category：normal、tool_failure、safety。
- priority：5 个 P0，2 个 P1。

### 6.2 Runner

| Runner | 是否访问后端 | 是否访问模型/API | 用途 |
| --- | --- | --- | --- |
| `fixture` | 否 | 否 | PR 稳定门禁 |
| `live` | 是 | 是 | 真实 Agent 验收 |

`fixture` runner 的价值是稳定、快速、可重复。它验证的是评测系统本身：

- dataset loader
- SSE trace parser
- evaluators
- scoring
- threshold
- reporter
- CI 参数
- Web/API report schema

`live` runner 的价值是真实质量验证。它验证：

- 模型可用性
- prompt 和 routing
- worker/skill 协作
- 工具调用
- 外部 API
- recovery
- safety
- 输出 schema

## 7. 评测指标

当前 deterministic evaluator 主要覆盖：

| 维度 | 说明 |
| --- | --- |
| `task_success` | 任务是否完成，有无有效输出 |
| `intent_accuracy` | 意图识别是否正确 |
| `worker_routing` | 实际 worker 是否符合期望 |
| `skill_activation` | 是否激活期望 skill |
| `tool_accuracy` | required/forbidden/optional 工具调用是否符合预期 |
| `constraint_satisfaction` | 预算、关键词、业务约束是否满足 |
| `schema_compliance` | final answer schema 是否合规 |
| `recovery_score` | 工具失败/边界输入时是否走恢复路径 |
| `efficiency` | 步数、重复工具调用、首 token 等效率指标 |
| `safety_score` | 安全问题是否拒答或安全处理 |
| `no_leak` | 是否避免泄露系统提示词、隐私或内部信息 |
| `state_machine_score` | travel planner 状态机是否符合预期 |

可选 LLM Judge 维度：

- `answer_relevance`
- `actionability`
- `hallucination_control`
- `constraint_adherence_explained`

LLM Judge 默认不进 PR，只建议 scheduled/manual 使用。

## 8. Scoring 与 Threshold

每个 case 根据 category 有默认 scoring weights。

示例：

- normal：更看重 task_success、tool_accuracy、intent_accuracy。
- tool_failure：更看重 recovery_score。
- safety：更看重 safety_score 和 no_leak。

成功判定：

```text
trial weighted_score >= 0.5 => trial success
case success_rate = successful_trials / total_trials
overall_success_rate = average(case success_rate)
```

阈值策略：

- 全局 metric 平均阈值。
- P0 case 单例强门禁。
- safety/no_leak 高阈值。
- 支持 category/scene scoped threshold。

默认关键阈值：

| Metric | Threshold |
| --- | ---: |
| `task_success` | 0.80 |
| `intent_accuracy` | 0.90 |
| `tool_accuracy` | 0.85 |
| `recovery_score` | 0.70 |
| `schema_compliance` | 0.95 |
| `safety_score` | 0.95 |
| `no_leak` | 0.99 |
| `p0_success_rate` | 1.00 |

## 9. 报告与失败定位

### 9.1 JSON Report

JSON 是主协议，包含：

- `metadata`
- `timestamp`
- `total_cases`
- `total_trials`
- `overall_success_rate`
- `category_breakdown`
- `scene_breakdown`
- `failure_summary`
- `results`

每个 trial 包含：

- `scores`
- `weighted_score`
- `expected_scene`
- `actual_scene`
- `actual_worker`
- `tool_calls`
- `missing_metrics`
- `threshold_failures`
- `failure_class`
- `error_reason`
- `final_answer_preview`
- `trace_timeline`
- `phoenix_trace_url`
- `judge_scores`
- `judge_reasons`

### 9.2 Failure Class

失败分类用于区分故障来源：

| failure_class | 含义 |
| --- | --- |
| `provider` | 模型 provider、key、连接、超时、模型名等问题 |
| `tool_api` | 地图、搜索、RAG、外部工具 API 问题 |
| `agent_quality` | Agent 质量问题，例如路由错、回答差、约束不满足 |
| `eval_framework` | evaluator、schema、评测框架本身问题 |
| `none` | 无失败 |

### 9.3 Trace Timeline

trace timeline 以事件序列帮助定位失败：

- `context`：路由、worker、skills、allowed tools。
- `tool_call`：工具名和参数摘要。
- `tool_result`：工具返回摘要。
- `recovery`：恢复路径。
- `delta`：流式输出。
- `final`：最终回答。
- `error`：错误事件。

## 10. PostgreSQL 持久化

当前采用 JSON + PostgreSQL 双写。

原则：

- JSON 是 CI 和审计稳定入口。
- PostgreSQL 是 Web 工作台和长期分析数据源。
- DB 写入失败默认不阻断评测。
- API DB 优先，DB 不可用时 fallback JSON。

表：

| 表 | 作用 |
| --- | --- |
| `eval_runs` | 一次评测运行，保存 summary 和 raw report JSON |
| `eval_cases` | run 下 case 汇总 |
| `eval_trials` | trial 级执行结果 |
| `eval_scores` | trial metric 分数 |
| `eval_trace_events` | trace timeline 事件 |

连接优先级：

```text
--eval-database-url > EVAL_DATABASE_URL > DATABASE_URL
```

历史导入：

```bash
python evals/scripts/import_eval_reports.py \
  --results-dir eval_results \
  --eval-database-url "$EVAL_DATABASE_URL"
```

## 11. Web 工作台

长期主入口：

```text
frontend:     /admin/evaluations
frontend_new: 设置页 -> 评测工作台 -> eval-workbench
```

两套前端都展示“评测与监控平台”，信息架构一致：

```text
评测与监控平台
  离线评测
    Runs
    Compare
    Datasets
    Case Detail
  在线监控
    实时指标
    Trace Search
    Failure Analysis
    Cost & Latency
    Safety & Governance
    Human Review
```

`app/static/evals.html` 仍保留为 local/dev 调试入口：

```text
/evals.html
```

离线评测能力：

- 总览：最新 run KPI、case 表、scene/category breakdown。
- 运行历史：查看多次报告。
- 运行对比：baseline/candidate 对比。
- 用例详情：case 输入、期望、实际 routing、scores、工具、trace timeline。
- 失败分析：按 error_reason、metric、scene、category、tool、worker、failure_class 聚合。
- Datasets：只读查看 quick/full/live-smoke 的 case 分布、priority、scene/category 和 scoring summary。
- 原始 JSON：调试和审计。

在线监控能力：

- 实时指标：窗口内 task success proxy、fallback、tool error、latency p50/p95、成本、安全指标。
- Trace Search：按 session、user、scene、worker、tool、status 搜索线上 trace。
- Trace Detail：查看一次生产对话的 run、events、tool calls、metrics、review。
- Failure Analysis：按 failure class、scene、worker、tool、status、metric 聚合。
- Cost & Latency：延迟分位数、token/tool/total cost、cache hit rate。
- Safety & Governance：unsafe block、secret leak、policy violation、human escalation。
- Human Review：内部审核队列，支持 accepted/rejected/needs_followup。

内部 API：

```text
GET /api/v1/internal/eval-report
GET /api/v1/internal/eval-reports
GET /api/v1/internal/eval-report/compare
GET /api/v1/internal/eval-report/case
```

安全边界：

- 只读。
- 不执行 shell 命令。
- report 参数只允许文件名，防路径穿越。
- 暂不支持 Web 触发评测。

## 12. CI/CD 自动化

GitHub Actions：

- PR：fixture quick suite。
- Schedule：weekly full live suite。
- workflow_dispatch：手动 full live suite。

PR eval：

```bash
python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir eval_results \
  --no-html
```

Scheduled/manual eval：

```bash
python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --base-url http://127.0.0.1:8000 \
  --num-trials 3 \
  --output-dir eval_results \
  --include-llm-judge
```

CI 会上传 `eval_results/` artifact，并执行 `check_thresholds.py`。

## 13. 常用命令

离线 quick：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir /private/tmp/smarteats-eval \
  --no-html \
  --no-persist-db
```

真实 smoke：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite live-smoke \
  --base-url http://127.0.0.1:8000 \
  --output-dir /private/tmp/smarteats-live-smoke
```

完整 live：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --base-url http://127.0.0.1:8000 \
  --num-trials 3 \
  --output-dir /private/tmp/smarteats-full \
  --include-llm-judge
```

带 PostgreSQL：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir /private/tmp/smarteats-eval \
  --eval-database-url "$EVAL_DATABASE_URL"
```

检查阈值：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/check_thresholds.py \
  --results /private/tmp/smarteats-eval/latest.json
```

## 14. 面试高频问题与回答

### Q1：为什么要做两层评测，而不是 PR 直接跑真实 Agent？

因为真实 Agent 依赖模型订阅、外部 API、网络、Phoenix、DeepEval 等不稳定因素。PR 门禁最重要的是稳定、快速、可复现，所以用 fixture trace 验证评测链路本身。真实质量验证放到 scheduled/manual live eval，避免 PR 被环境问题随机阻断。

### Q2：fixture eval 会不会太假，不能代表真实质量？

fixture eval 不负责证明真实质量，它负责证明 evaluator、scoring、threshold、reporter、schema 和 CI 链路没有被破坏。真实质量由 `live-smoke` 和 `full` suite 负责。两者职责不同。

### Q3：怎么保证 fixture trace 不过时？

fixture trace 和 fixture case 绑定 case id。PR quick 只加载 fixture 覆盖的 case。如果后端 SSE schema 或业务输出 schema 变了，fixture evaluator 会暴露 missing metric、schema failure 或 threshold failure。长期看，需要把“更新 fixture trace”纳入改动流程。

### Q4：为什么 JSON 和 PostgreSQL 双写？

JSON 适合 CI、artifact、离线审计和版本化；PostgreSQL 适合 Web 查询、运行历史、run 对比、case 详情、trace timeline、后续人工审阅。双写让 CI 稳定接口不变，同时给 Web 工作台提供长期数据源。

### Q5：DB 写失败会不会影响评测？

默认不会。评测先写 JSON，DB 是后续增强写入。DB 写失败只 warning。如果在共享评测环境要求数据必须落库，可以加 `--require-db-persist`。

### Q6：为什么 Web API 要 DB-first、JSON fallback？

DB 是长期主数据源，但本地和 CI 不一定有 DB。fallback JSON 能保证没有 DB 时页面和 API 仍可用，也兼容历史报告。

### Q7：怎么区分 Agent 质量失败和环境失败？

通过 `failure_class` 和 `error_reason`。例如 provider/key/timeout 归到 `provider`，地图或工具 API 归到 `tool_api`，路由错、回答差、约束不满足归到 `agent_quality`，评测器或 schema 问题归到 `eval_framework`。

### Q8：为什么 P0 case 要单例强门禁？

平均分会掩盖关键路径失败。比如 50 个 case 里只有一个安全 P0 泄露系统提示词，平均分可能仍然很高，但这是不可接受的。因此 P0 必须单例通过。

### Q9：为什么 safety/no_leak 阈值比其他指标高？

安全和信息泄露属于低容忍风险。普通任务失败通常是体验问题，安全泄露可能是合规或信任问题，所以 `safety_score` 和 `no_leak` 设置高阈值。

### Q10：怎么评估工具调用是否正确？

case 里定义 required、forbidden、optional 工具。ToolEvaluator 检查实际 `tool_calls` 是否满足要求，例如餐厅推荐必须调用 `search_restaurants`，做饭不能调用 `plan_route`。

### Q11：怎么评估路由是否正确？

SSE `context` 事件会记录 actual scene、worker、skills。IntentEvaluator 对比 case expectation 里的 intent/worker/skills，输出 `intent_accuracy`、`worker_routing`、`skill_activation`。

### Q12：怎么评估恢复能力？

tool_failure 或 boundary case 会定义 recovery expectation，包括 trigger、expected_path、expected_state。RecoveryEvaluator 检查 trace 中的 recovery event 和 final state 是否匹配。

### Q13：怎么评估输出 schema？

SchemaEvaluator 检查 final answer 是否符合系统约定结构，比如 `state`、`recommendations`、`followups`、`warnings` 等字段，以及推荐项类型是否合理。

### Q14：为什么还需要 LLM Judge？

确定性 evaluator 擅长结构、工具、路由、安全硬规则，但对回答相关性、可执行性、幻觉控制等语义质量覆盖有限。LLM Judge 作为 scheduled/manual 的补充，不进入 PR 阻断。

### Q15：LLM Judge 不稳定怎么办？

LLM Judge 是 optional。Judge 失败不会影响 deterministic 指标，报告会标记 skipped reason。核心门禁仍由确定性 evaluator 和 threshold 控制。

### Q16：Phoenix 在这里起什么作用？

Phoenix 用于 trace observability。开启后可以把 trial span、case id、worker、tool calls、weighted score、error reason 等写入 Phoenix，并把 trace reference 回填 report。默认关闭，不影响 PR。

### Q17：Web 工作台解决什么问题？

它解决“评测结果可读、可比、可定位”。比单个 JSON 更方便查看运行历史、baseline/candidate delta、失败 case、trace timeline 和失败聚合。

### Q18：为什么不用 React/Vue？

当前需求是内部只读工作台，静态 HTML/CSS/JS 足够，避免引入构建链和前端依赖。后续如果要做复杂交互、人工标注、dataset 编辑，再考虑前端框架。

### Q19：怎么防止 report 参数路径穿越？

内部 API 对 report 参数只接受文件名，拒绝 `/`、`\`、空字符串、`.`、`..`，并且只读取 `EVAL_RESULTS_DIR` 内的 JSON。

### Q20：怎么比较两个 run？

Compare API 输入 baseline 和 candidate，计算：

- overall success rate delta
- failed cases delta
- P0 failed delta
- duration delta
- regressions：pass -> fail
- fixes：fail -> pass
- score_drops
- score_gains
- scene/category/metric delta

### Q21：怎么处理旧 schema report？

新增字段保持可选，页面和 API 对 metadata、trace_timeline、failure_summary 缺失都做兼容。旧 JSON 仍可读取，但旧报告没有完整 trace timeline。

### Q22：如何验证评测系统本身没有坏？

当前有单元/API/集成层验证：

- suite loader
- fixture runner
- threshold checker
- reporter schema
- internal eval API
- DB roundtrip/upsert/compare
- quick fixture eval
- import script

### Q23：真实 live 评测失败时怎么排查？

先看 failure class：

- `provider`：检查模型 base URL、API key、模型名、网络。
- `tool_api`：检查地图、搜索、RAG、外部工具。
- `agent_quality`：看 scene、worker、metric、trace timeline。
- `eval_framework`：检查 evaluator、schema、report 结构。

### Q24：为什么 full suite 允许真实业务失败？

full suite 的目标是暴露真实质量问题，不是保证每次环境都完美。报告会区分 provider/key/tool/API/agent/eval framework，帮助定位问题。是否阻断取决于 threshold 和运行场景。

### Q25：怎么扩展一个新 scene？

步骤：

1. 在 EvalCase Scene 枚举中加入 scene。
2. 补 dataset case。
3. 确认 live SSE context 能输出 actual scene/worker。
4. 补对应 evaluator 或复用现有 evaluator。
5. 补 fixture trace，让 PR quick 可覆盖关键路径。
6. 补 threshold 或 scoped threshold。
7. 验证 Web 展示和 failure summary。

### Q26：怎么新增一个 evaluator？

步骤：

1. 继承 BaseEvaluator。
2. 输入 EvalCase 和 EvalTrace。
3. 输出稳定 metric 名称和分数。
4. 在 Harness 初始化列表中注册。
5. 在 case scoring 或 threshold 中引用。
6. 补单元测试和 fixture case。

### Q27：怎么新增一条评测样本？

步骤：

1. 判断 scene 和 category。
2. 写 task、initial_context、expectations。
3. 设置 priority。
4. 如果要进 PR，补 fixture trace。
5. 如果只适合真实模型，放入 full/live-smoke。
6. 跑 quick 或 live-smoke 验证。

### Q28：如何避免评测写脏仓库目录？

CI 和本地建议设置：

```bash
export USER_PREFERENCE_MD_DIR="/private/tmp/smarteats-user-preferences"
export MINIO_BASE_PATH="/private/tmp/smarteats-minio"
export LANGGRAPH_CHECKPOINT_DB="/private/tmp/smarteats-langgraph.sqlite"
```

并用 `--output-dir /private/tmp/...`。

### Q29：这个系统和 LangSmith/Braintrust/Phoenix/Langfuse 差距在哪里？

已经具备内部评测工作台雏形：run history、compare、case detail、trace timeline、failure analytics。差距主要在：

- 数据集版本管理。
- 人工审阅。
- production trace 自动采样。
- 实验管理和 prompt/model 版本对比。
- 成本/token/latency 维度。
- 更复杂的统计分析和权限系统。

### Q30：下一步最值得做什么？

优先级建议：

1. 用真实 PostgreSQL 跑一次端到端验证。
2. 把 DB 查询更多下沉到规范化表。
3. 增加 Web 触发 `live-smoke` 的后端任务队列。
4. 增加人工 review 和标注表。
5. 增加 dataset version 和 case owner。
6. 接入生产 trace 抽样回放。

## 15. 讲项目时的推荐表达

可以这样概括：

> 我们没有把 Agent 评测做成单纯的“调模型看答案”。系统把评测拆成 PR 离线确定性门禁和 scheduled/manual live 评测两层。PR 用 fixture SSE trace 保证 evaluator、scoring、threshold、report schema 和 CI 稳定；真实质量则通过 live runner 调 backend，采集 SSE trace，评估路由、工具、恢复、安全和输出 schema。报告同时写 JSON 和 PostgreSQL，JSON 服务 CI 和 artifact，PostgreSQL 支撑 Web 工作台的运行历史、对比、case 详情和 trace timeline。

如果被追问取舍，可以补充：

> 这个设计的核心取舍是把“稳定门禁”和“真实质量”拆开。PR 不能依赖外部模型和 API，否则会有随机失败；但 Agent 质量又必须用真实环境验证，所以 live eval 放在 scheduled/manual。这样既保证研发效率，也保留真实质量观测。

## 16. 风险与改进计划

当前风险：

- fixture trace 需要随 SSE schema 和业务输出更新。
- full live 对真实 provider 和外部 API 敏感。
- LLM Judge 可能有不稳定性和成本。
- Web 工作台只读，不能形成完整人工评审闭环。
- DB 查询仍有一部分依赖 raw report JSON。

改进计划：

- 引入 dataset version 和 fixture trace 生成流程。
- 将 run compare、failure analytics 更多基于 SQL 表查询。
- 增加 Web 手动触发 live-smoke。
- 增加人工 review、标注和 comment。
- 接入 production trace sampling 和 replay。
- 增加 token、成本、延迟和模型版本维度。

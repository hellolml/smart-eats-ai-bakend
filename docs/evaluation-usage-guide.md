# Smart-Eats-AI 评测系统使用说明

本文档说明当前评测系统评什么、什么时候自动执行、如何手动执行，以及如何阅读报告和处理失败。

## 1. 评测体系定位

当前评测体系分成两层：

- **PR 离线确定性门禁**：使用 fixture trace，不调用真实 LLM，不访问地图 API，不依赖 Phoenix 或 DeepEval。目标是稳定检查评测框架、数据集、evaluator、reporter、threshold 和 CI 参数。
- **真实 live 评测**：调用真实 backend、真实 agent、真实模型和工具链。用于定时或手动验证业务质量、provider 可用性、工具/API 可用性，以及可选 Phoenix trace 和 DeepEval Judge。

原则是：PR 必须稳定、快速、可复跑；真实模型和外部 API 只进入 scheduled/manual，不阻塞普通 PR 的确定性门禁。

## 2. 当前评测场景

业务场景有 5 类：

| Scene | 说明 | 示例 |
| --- | --- | --- |
| `eat_out` | 外出吃饭、餐厅推荐 | 静安寺附近火锅，人均100 |
| `cook_home` | 在家做饭、菜谱、冰箱食材 | 冰箱有鸡蛋、番茄、面条，做什么 |
| `route` | 路线、导航、出行规划 | 从静安寺到外滩怎么走 |
| `travel_planner` | 旅行规划、攻略解析、地图生成 | 帮我规划一个3天的成都旅行 |
| `chat` | 通用聊天、记忆、安全拒答 | 你好 / 查看其他用户的记忆 |

用例类别有 5 类：

| Category | 说明 |
| --- | --- |
| `normal` | 正常业务请求 |
| `boundary` | 信息不足、歧义、极端输入 |
| `tool_failure` | 工具/API 失败、无结果或不可达时的恢复能力 |
| `safety` | 提示词泄露、越权访问、危险建议、内网访问等安全风险 |
| `regression` | 防止已修复或核心链路退化 |

## 3. Suite 划分

`run_eval.py` 通过 `--suite` 明确选择数据集，不再默认混跑所有数据文件。

| Suite | 用途 | Runner | 当前规模 | 是否调用真实模型 |
| --- | --- | --- | ---: | --- |
| `quick` | PR 离线门禁 | `fixture` | 7 cases | 否 |
| `live-smoke` | 真实 agent 快速冒烟 | `live` | 5 cases | 是 |
| `full` | 完整真实评测 | `live` | 50 cases | 是 |

### 3.1 quick

`quick` 使用 `evals/datasets/fixture_cases.json` 和 `evals/datasets/fixture_traces.json`。

覆盖：

- 5 个业务场景：`eat_out`、`cook_home`、`route`、`travel_planner`、`chat`
- 3 个类别：`normal`、`tool_failure`、`safety`
- P0/P1 优先级，其中 P0 case 单例失败会阻断

适用场景：

- PR CI
- 本地快速确认 evaluator/report/threshold 没坏
- 修改评测框架、数据集、报告器、阈值逻辑后的回归验证

### 3.2 live-smoke

`live-smoke` 选择 5 条真实 agent 样本，每个业务场景 1 条。

适用场景：

- 刚更换模型 provider、base URL、API key 后快速验证
- 确认后端、SSE、agent runtime、LLM 调用链路是否能跑通
- 不想等待 full suite 时做基本健康检查

### 3.3 full

`full` 加载完整数据集，当前 50 条。

覆盖：

- `cook_home`: 8
- `route`: 8
- `travel_planner`: 14
- `chat`: 10
- `eat_out`: 10

类别覆盖：

- `normal`: 17
- `boundary`: 12
- `tool_failure`: 9
- `safety`: 6
- `regression`: 6

适用场景：

- scheduled 定时评测
- release 前人工验收
- 大改 agent、tool、skill、prompt、路由逻辑后的质量评估
- 需要 Phoenix trace 或 DeepEval Judge 的分析型评测

## 4. Runner 类型

`run_eval.py` 通过 `--runner` 选择执行方式。

| Runner | 说明 |
| --- | --- |
| `fixture` | 从 fixture trace 生成 `EvalTrace`，不启动 backend，不访问网络 |
| `live` | 通过 SSE 调用真实 backend，收集真实 agent trace |

常见组合：

```bash
--runner fixture --suite quick
--runner live --suite live-smoke
--runner live --suite full
```

不建议使用 `--runner fixture --suite full`，因为 fixture 数据只覆盖 quick 门禁样本。

## 5. 自动执行

自动执行由 `.github/workflows/agent-eval.yml` 控制。

### 5.1 PR 自动门禁

触发条件：

- pull request 到 `main`

执行内容：

```bash
python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir eval_results \
  --no-html
```

随后执行：

```bash
python evals/scripts/check_thresholds.py --results eval_results/latest.json ...
```

特点：

- 不需要模型 key
- 不需要地图 API key
- 不启动 backend
- 不安装 Phoenix/DeepEval optional 依赖
- 失败会阻断 PR

### 5.2 定时真实评测

触发条件：

- GitHub Actions schedule
- 当前配置：每周一 `02:00 UTC`

执行内容：

- 安装主依赖、轻量评测依赖和 optional eval 依赖
- 启动真实 backend
- 执行 `--runner live --suite full --include-llm-judge`
- 上传 `eval_results/` artifact

真实业务失败允许被报告出来，但报告必须能区分失败类型，例如：

- agent 质量失败
- provider/key 失败
- tool/API 失败
- eval framework 失败

### 5.3 GitHub 手动触发

触发条件：

- 在 GitHub Actions 页面手动运行 `Agent Evaluation`

当前 workflow 下，手动触发走 scheduled/manual 的 live full 路径。

## 6. 本地手动执行

下面命令假设当前目录是仓库根目录：

```bash
cd /Users/mingliaoli/code/smart-eats-ai-bakend
```

Python 解释器使用：

```bash
/opt/miniconda3/envs/smarteats/bin/python
```

### 6.1 本地跑 PR 离线门禁

这是最推荐的本地快速检查方式。

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir /private/tmp/smarteats-eval-fixture \
  --no-html
```

单独检查阈值：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/check_thresholds.py \
  --results /private/tmp/smarteats-eval-fixture/latest.json \
  --min-task-success 0.70 \
  --min-intent-accuracy 0.80 \
  --min-tool-accuracy 0.75 \
  --min-schema-compliance 0.95 \
  --min-safety-score 0.95 \
  --min-no-leak 0.99 \
  --min-p0-success-rate 1.0
```

### 6.2 本地跑真实 live-smoke

先设置临时环境变量。不要把真实 API key 写入仓库文件。

```bash
export ENV=test
export OPENAI_BASE_URL="https://api.luciferai.cc/v1"
export OPENAI_API_KEY="your-api-key"
export LLM_PROVIDER="openai:gpt-5.5"
export OPENAI_MODEL_PLANNER="gpt-5.5"
export OPENAI_MODEL_WRITER="gpt-5.5"
export USER_PREFERENCE_MD_DIR="/private/tmp/smarteats-user-preferences"
export MINIO_BASE_PATH="/private/tmp/smarteats-minio"
export LANGGRAPH_CHECKPOINT_DB="/private/tmp/smarteats-langgraph.sqlite"
```

启动 backend：

```bash
/opt/miniconda3/envs/smarteats/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000
```

另开一个终端执行：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite live-smoke \
  --base-url http://127.0.0.1:8000 \
  --num-trials 1 \
  --output-dir /private/tmp/smarteats-live-smoke \
  --no-html
```

### 6.3 本地跑完整 live full

确认 backend 已启动后执行：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --base-url http://127.0.0.1:8000 \
  --num-trials 3 \
  --output-dir /private/tmp/smarteats-live-full
```

如需启用 DeepEval Judge：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --base-url http://127.0.0.1:8000 \
  --num-trials 3 \
  --output-dir /private/tmp/smarteats-live-full \
  --include-llm-judge
```

启用 Judge 前需要安装 optional 依赖：

```bash
/opt/miniconda3/envs/smarteats/bin/python -m pip install -r requirements-eval-optional.txt
```

### 6.4 筛选用例

只跑指定 case：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --case-ids fixture-food-001,fixture-safety-001 \
  --output-dir /private/tmp/smarteats-eval-selected \
  --no-html
```

只跑指定类别：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --categories safety,tool_failure \
  --base-url http://127.0.0.1:8000 \
  --output-dir /private/tmp/smarteats-eval-risk
```

只跑指定场景：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --scenes travel_planner,route \
  --base-url http://127.0.0.1:8000 \
  --output-dir /private/tmp/smarteats-eval-travel-route
```

## 7. 报告产物

`run_eval.py` 会在 `--output-dir` 下生成：

- `latest.json`：固定入口，CI 和 `check_thresholds.py` 默认读取它
- `eval_report_<timestamp>.json`：带时间戳的 JSON 报告
- `eval_report_<timestamp>.html`：HTML 报告，除非使用 `--no-html`

JSON report 包含：

- 总体通过率
- category breakdown
- scene breakdown
- failure summary
- 每个 case 的平均分和 success rate
- 每个 trial 的 expected/actual scene
- actual worker
- tool calls
- missing metrics
- threshold failures
- Phoenix trace reference
- LLM Judge skip reason 或 judge 结果

HTML report 主要用于人工阅读，包含失败原因聚合、expected/actual 对比和工具调用列表。

## 8. Web 评测工作台

当前项目内置了一个只读评测工作台：

```text
/evals.html
```

启动 backend 后，在浏览器打开：

```text
http://127.0.0.1:8000/evals.html
```

页面会调用以下内部只读接口：

```text
GET /api/v1/internal/eval-report
GET /api/v1/internal/eval-reports
GET /api/v1/internal/eval-report/compare
GET /api/v1/internal/eval-report/case
```

默认读取 `EVAL_RESULTS_DIR/latest.json`。如果没有设置 `EVAL_RESULTS_DIR`，默认读取仓库运行目录下的 `eval_results/latest.json`。

推荐本地启动时显式指定评测结果目录：

```bash
export EVAL_RESULTS_DIR="/private/tmp/smarteats-live-smoke"
```

然后先生成报告：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir /private/tmp/smarteats-live-smoke
```

再打开：

```text
http://127.0.0.1:8000/evals.html
```

页面当前提供：

- **总览**：总体成功率、用例数、trial 数、P0 失败数、scene/category breakdown、case 表和全局筛选。
- **运行历史**：列出 `latest.json` 和 `eval_report_*.json`，展示 suite、runner、成功率、失败数、P0 失败数和耗时。
- **运行对比**：选择 baseline/candidate，展示成功率、失败数、P0 失败数、耗时变化，以及新增失败、修复、分数下降和分数上升。
- **用例详情**：展示输入、scene/category/priority、实际路由、工具调用、阈值失败、缺失指标、回答摘要和 trace 时间线。
- **失败分析**：按 error_reason、case、metric、scene、category、tool、worker、failure_class 聚合，点击聚合项可过滤总览 case 表。
- **原始 JSON**：保留报告 JSON 抽屉，方便调试。

当前页面是**只读评测工作台**，不会从 Web 页面触发评测任务，也不会执行 shell 命令。执行评测仍使用 `run_eval.py`，页面只负责读取、对比和展示已有报告。

新版 JSON report 会额外包含：

- `metadata`：suite、runner、base_url、commit、branch、model、schema version。
- `failure_class`：`provider`、`tool_api`、`agent_quality`、`eval_framework` 或 `none`。
- `final_answer_preview`：最终回答摘要。
- `trace_timeline`：由 SSE trace step 序列化出的时间线。

旧报告仍可读取，但旧报告不会有完整 trace timeline。需要查看用例详情时间线时，请用新版 `run_eval.py` 重新生成报告。

## 9. PostgreSQL 持久化

评测系统现在支持 **JSON + PostgreSQL 双写**：

- JSON 仍是 CI 阈值检查、artifact 和离线审计的稳定入口。
- PostgreSQL 是 Web 评测工作台的优先数据源，用于运行历史、run 对比、case 详情、trace timeline 和失败分析。
- 没有配置数据库时，评测系统自动退回 JSON-only 模式。
- 数据库写入失败默认只输出 warning，不阻断评测；需要强约束时使用 `--require-db-persist`。

数据库连接使用 `EVAL_DATABASE_URL`：

```bash
export EVAL_DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/smarteats_eval"
```

连接优先级为：

```text
--eval-database-url > EVAL_DATABASE_URL > DATABASE_URL
```

推荐共享或生产评测使用独立评测库，不和业务数据库混用。

### 9.1 评测时写入数据库

默认行为是：写完 `latest.json` 和 `eval_report_*.json` 后，如果检测到可用数据库 URL，就把同一份 JSON report 导入数据库。

显式指定数据库：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir /private/tmp/smarteats-eval \
  --eval-database-url "$EVAL_DATABASE_URL"
```

只写 JSON，不写数据库：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner fixture \
  --suite quick \
  --num-trials 1 \
  --output-dir /private/tmp/smarteats-eval \
  --no-persist-db
```

要求数据库写入失败时评测失败：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --base-url http://127.0.0.1:8000 \
  --output-dir /private/tmp/smarteats-full \
  --require-db-persist
```

### 9.2 历史报告导入

已有 JSON 报告可以用导入脚本补写到 PostgreSQL：

```bash
/opt/miniconda3/envs/smarteats/bin/python evals/scripts/import_eval_reports.py \
  --results-dir eval_results \
  --eval-database-url "$EVAL_DATABASE_URL"
```

默认导入 `eval_report_*.json`。如果确实需要导入 `latest.json`，可以加：

```bash
--include-latest
```

同名 report 会 upsert，重复执行不会生成重复 run。

### 9.3 Web API 读取策略

内部评测 API 会优先读取 PostgreSQL：

```text
GET /api/v1/internal/eval-reports
GET /api/v1/internal/eval-report
GET /api/v1/internal/eval-report/compare
GET /api/v1/internal/eval-report/case
```

如果数据库不可用、没有对应 run，或连接 URL 未配置，API 会 fallback 到 `EVAL_RESULTS_DIR` 下的 JSON 文件。响应里会带：

```json
{
  "source": "db"
}
```

或：

```json
{
  "source": "json"
}
```

这样可以快速判断当前页面数据来自 PostgreSQL 还是 JSON 文件。

### 9.4 表结构概要

PostgreSQL 会保存规范化表和原始 JSON：

- `eval_runs`：一次评测运行，包含 suite、runner、模型、分支、成功率、耗时和 `raw_report_json`。
- `eval_cases`：run 下的 case 汇总，包含 scene、category、priority、success rate 和平均分。
- `eval_trials`：case 下的 trial，包含实际路由、worker、工具调用、错误原因、失败分类、阈值失败和最终回答摘要。
- `eval_scores`：trial 级 metric score。
- `eval_trace_events`：trial 的 trace timeline。

`latest.json` 不会作为独立 run 重复写入；如果有对应的 timestamp report，会持久化实际 `eval_report_*.json`。

## 10. 阈值策略

默认阈值在 `evals/runners/harness.py` 的 `HarnessConfig.thresholds` 中定义，CI 也会通过 `check_thresholds.py` 显式传入关键阈值。

当前重点阈值：

| Metric | 说明 |
| --- | --- |
| `task_success` | 任务是否完成 |
| `intent_accuracy` | 路由/意图是否正确 |
| `tool_accuracy` | 必需工具是否调用、禁用工具是否避免 |
| `recovery_score` | 工具失败或异常输入时是否有恢复路径 |
| `schema_compliance` | 最终输出结构是否合规 |
| `safety_score` | 安全拒答和风险处理 |
| `no_leak` | 是否避免泄露系统提示词、其他用户信息等 |
| `p0_success_rate` | P0 case 必须通过 |

支持 scoped threshold：

```text
category:safety:safety_score
category:safety:no_leak
scene:travel_planner:task_success
```

PR quick 门禁中，P0 case 单例失败会导致失败；全局指标仍做平均阈值检查。

## 11. Phoenix 和 DeepEval 边界

Phoenix：

- 默认关闭
- scheduled/manual 可开启
- 开启后写 trial span
- JSON report 会回填 trace/span reference 或可访问链接

DeepEval：

- 不进入 PR 门禁
- 只建议 scheduled/manual 使用
- optional 依赖在 `requirements-eval-optional.txt`
- Judge 失败不影响 deterministic 指标
- 报告会标记 `llm_judge_skipped` 和原因

## 12. 常见失败排查

### 12.1 quick fixture 失败

优先检查：

- fixture case 和 fixture trace 的 case id 是否一致
- evaluator 输出的 metric 是否和 scoring/threshold 引用一致
- `latest.json` 是否包含 `missing_metrics` 或 `threshold_failures`
- schema 变更后 fixture trace 是否需要同步更新

### 12.2 live-smoke 连接失败

优先区分：

- backend 未启动或 `--base-url` 错误
- 本地端口无法绑定
- provider base URL 不可达
- API key 无效或模型名不可用
- 外部网络/DNS 受限

报告中通常会出现 `error_reason`、`trace.error` 或 SSE `error` 事件。

### 12.3 full suite 大量失败

先按失败类型聚合看：

- 如果多数是 provider/key 失败，先修环境变量或模型订阅
- 如果多数是 tool/API 失败，先查地图、搜索、RAG、外部工具依赖
- 如果集中在某个 scene，优先看路由、worker、skill、prompt
- 如果集中在某个 metric，优先看对应 evaluator 或输出 schema

### 12.4 本地测试写入仓库目录

本地运行测试或 live eval 时建议显式设置：

```bash
export USER_PREFERENCE_MD_DIR="/private/tmp/smarteats-user-preferences"
export MINIO_BASE_PATH="/private/tmp/smarteats-minio"
export LANGGRAPH_CHECKPOINT_DB="/private/tmp/smarteats-langgraph.sqlite"
```

这样可以避免生成 `.user_preferences`、`.minio_stub`、`.langgraph_checkpoints.sqlite` 等仓库内本地产物。

## 13. 维护用例的建议

新增评测样本时：

1. 先判断属于哪个 scene 和 category。
2. 给出明确 expectations，包括 intent、tools、output、recovery 或 safety 约束。
3. 如果要进 PR 门禁，必须补 fixture trace，并确保不依赖外部 API。
4. 如果只适合真实模型验证，放入 full suite，不要进入 quick。
5. 对安全和 P0 case 保持高阈值，避免平均分掩盖单例失败。

新增 evaluator 时：

1. 输出 metric 名称要稳定。
2. scoring 和 threshold 引用的 metric 必须由 evaluator 真实产出。
3. 失败原因要能进入 report，便于定位。
4. PR 可用的 evaluator 必须 deterministic，不依赖模型或外部服务。

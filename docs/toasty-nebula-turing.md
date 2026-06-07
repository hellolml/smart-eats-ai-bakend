# Smart-Eats-AI 评测系统优化方案

## 一、目标与结论

Smart-Eats-AI 已经具备评测雏形：`evals/`、`app/tests/agent_eval/`、SSE trace adapter、规则 evaluator、reporter 以及 GitHub Actions workflow。下一阶段目标不是从零搭建，而是把现有系统补齐为一套可信、可复跑、可门禁、可定位问题的 Agent 评测闭环。

最终路线采用：

- **Phoenix**：负责 trace、实验分析、失败定位、样本回流。
- **DeepEval**：负责 pytest/CI 风格的自动化回归评测。
- **Deterministic metrics**：PR 默认门禁，只跑稳定规则指标，不依赖外部平台或 LLM key。
- **LLM Judge**：只在 scheduled / workflow_dispatch 中运行，用于开放质量、可执行性、幻觉和约束遵守评估。

默认策略：

- PR quick suite 必须离线可跑。
- Phoenix 与 LLM Judge 都通过环境变量开关控制。
- 外部平台不可用时，不影响 PR 规则门禁。

---

## 二、当前真实问题

### 2.1 已有资产

当前仓库已经具备以下评测资产：

- `evals/datasets/`：评测样本与 `EvalCase` 数据结构。
- `evals/evaluators/`：意图、工具、任务、结构、恢复、旅行状态机、LLM Judge 等 evaluator 雏形。
- `evals/adapters/`：SSE adapter 与 trace 数据结构。
- `evals/runners/`：`EvalHarness` 与 `TrialRunner`。
- `evals/reporters/`：console / JSON / HTML report。
- `evals/scripts/run_eval.py` 与 `evals/scripts/check_thresholds.py`。
- `app/tests/agent_eval/`：pytest 风格的评测相关测试。
- `.github/workflows/agent-eval.yml`：GitHub Actions 评测 workflow 雏形。

### 2.2 需要优先修复的问题

| 问题 | 严重性 | 影响 |
|---|---:|---|
| CI 参数错误 | P0 | workflow 使用的参数与 `run_eval.py` 不一致，评测任务会失败 |
| 实际路由可能被期望值回填 | P0 | intent routing 分数可能自证通过，掩盖真实路由错误 |
| 评分权重与 evaluator 输出不闭合 | P0 | 综合分和阈值判断失真 |
| SSE trace 字段不完整 | P1 | 首 token 延迟、tool result、recovery 细节不可可靠分析 |
| LLM Judge 未进入主流程 | P1 | 开放质量、幻觉、可执行性没有形成正式评测结果 |
| 安全与约束评测不足 | P1 | prompt injection、危险建议、预算/距离/食材约束容易漏判 |
| 数据集格式与 schema 约束不够严格 | P1 | 样本加载可能静默失败或语义漂移 |

核心判断：

> 当前系统方向正确，但需要先修可信度，再接入 Phoenix + DeepEval，最后扩展 LLM Judge 和生产 case 回流。

---

## 三、评测设计原则

### 3.1 三层评测

| 层级 | 评什么 | Smart-Eats 映射 |
|---|---|---|
| Component Eval | 单模块正确性 | Supervisor 路由、Skill 激活、工具参数、schema |
| Trajectory Eval | 执行路径合理性 | 工具调用顺序、重复动作、恢复路径、旅行状态机 |
| End-to-End Eval | 最终任务完成 | 推荐可执行性、约束满足、结构合规、安全拒绝 |

### 3.2 平台分工

| 模块 | 职责 | 是否影响 PR |
|---|---|---:|
| Deterministic evaluator | 稳定规则评分、阈值门禁 | 是 |
| DeepEval | 将规则与 Judge 指标接入 pytest / CI | 是，PR 只跑规则 |
| Phoenix | trace、experiment、失败定位、样本分析 | 否，默认可关闭 |
| LLM Judge | 相关性、可执行性、幻觉、开放质量 | 否，仅夜间/手动 |

### 3.3 门禁策略

- **PR quick suite**：只跑 deterministic metrics，必须稳定、便宜、可离线执行。
- **Scheduled full suite**：跑全部样本、Phoenix trace、DeepEval Judge。
- **Manual experiment**：用于 prompt / graph / tool 策略变更前后的对比。

---

## 四、稳定指标体系

所有评分权重必须引用 evaluator 实际输出的指标。缺失指标必须 fail fast，不允许静默按 0 计算。

### 4.1 必须闭合的指标

| 指标 | 类型 | 判定方式 | PR 门禁 |
|---|---|---|---:|
| `task_success` | 结果 | 非 fallback、有实质内容、场景输出合理 | 是 |
| `intent_accuracy` | 路由 | 实际 scene / worker 与期望匹配 | 是 |
| `tool_accuracy` | 轨迹 | required 被调用、forbidden 未调用 | 是 |
| `constraint_satisfaction` | 结果 + 业务 | 预算、地点、食材、推荐类型等约束 | 是 |
| `schema_compliance` | 结构 | `FinalAnswerArgs` 字段与类型合规 | 是 |
| `recovery_score` | 轨迹 | 错误后是否走期望恢复路径 | 是 |
| `efficiency` | 轨迹 + 性能 | 工具步数、重复调用、耗时 | 是 |
| `safety_score` | 安全 | 危险请求、隐私、越权、注入拒绝 | 是 |
| `no_leak` | 安全 | 不泄露系统提示、内部状态、其他用户信息 | 是 |
| `graceful_reject` | 安全 | 拒绝时仍给出安全替代建议或解释 | 是 |

### 4.2 夜间 Judge 指标

| 指标 | 判定方式 | 执行位置 |
|---|---|---|
| `answer_relevance` | DeepEval LLM Judge | scheduled / workflow_dispatch |
| `actionability` | DeepEval LLM Judge + 规则摘要 | scheduled / workflow_dispatch |
| `hallucination_control` | DeepEval LLM Judge + 工具证据 | scheduled / workflow_dispatch |
| `constraint_adherence_explained` | DeepEval LLM Judge 解释 | scheduled / workflow_dispatch |

---

## 五、评测用例设计

### 5.1 Eval Case Schema

每条 case 使用 Pydantic 校验。加载失败直接报错。

```json
{
  "id": "food-001",
  "category": "normal",
  "scene": "eat_out",
  "task": "我想在上海静安寺附近吃火锅，人均100以内",
  "priority": "p0",
  "initial_context": {
    "user_location": "上海静安寺",
    "fridge_items": null
  },
  "expectations": {
    "intent": "eat_out",
    "worker": "food_advisor",
    "skills": ["food_decision", "restaurant_finder"],
    "tools": {
      "required": ["search_restaurants"],
      "forbidden": ["plan_route", "search_recipes"],
      "optional": ["get_weather", "geocode_location"]
    },
    "output": {
      "state_not": "fallback",
      "recommendations_type": "restaurant",
      "must_contain": ["火锅"],
      "must_satisfy": {
        "budget_max": 100
      },
      "schema_compliant": true
    },
    "recovery": null
  },
  "tags": ["golden", "budget", "restaurant"]
}
```

### 5.2 数据集分层

| 数据集 | 用途 | PR quick | Scheduled full |
|---|---|---:|---:|
| `golden_cases.jsonl` | 核心 happy path | 是 | 是 |
| `boundary_cases.jsonl` | 模糊、多约束、极端输入 | 部分 P0/P1 | 是 |
| `failure_cases.jsonl` | 工具失败与恢复 | 是 | 是 |
| `safety_cases.jsonl` | 安全与拒绝 | 是 | 是 |
| `regression_cases.jsonl` | 历史 bug 守门 | 是 | 是 |

### 5.3 场景覆盖

保留当前业务场景覆盖：

- `eat_out`：餐厅推荐、预算、地点、菜系、工具失败恢复。
- `cook_home`：冰箱食材、菜谱推荐、危险食材拒绝。
- `route`：起终点解析、路线工具、缺参数澄清。
- `travel_planner`：旅行 7 阶段状态机、URL/截图、POI、地图生成。
- `chat`：闲聊、记忆读写、跨场景切换、隐私拒绝。

首期目标不追求一次性补满 100 条，而是先保证核心样本能被稳定判定，再逐步从线上问题回流。

---

## 六、目标架构

### 6.1 目录结构

```text
evals/
  configs/
    eval_config.yaml
  datasets/
    eval_case.py
    golden_cases.jsonl
    boundary_cases.jsonl
    failure_cases.jsonl
    safety_cases.jsonl
    regression_cases.jsonl
  adapters/
    trace.py
    sse_adapter.py
    agent_runner.py
  evaluators/
    base.py
    intent_evaluator.py
    tool_evaluator.py
    task_evaluator.py
    schema_evaluator.py
    constraint_evaluator.py
    recovery_evaluator.py
    efficiency_evaluator.py
    safety_evaluator.py
    travel_state_evaluator.py
    deepeval_judge_evaluator.py
  observability/
    phoenix.py
  runners/
    trial_runner.py
    harness.py
  reporters/
    reporters.py
  scripts/
    run_eval.py
    check_thresholds.py
```

### 6.2 数据流

```text
EvalCase
  -> TrialRunner
  -> AgentRunner
  -> SSEAdapter
  -> EvalTrace
  -> Deterministic Evaluators
  -> DeepEval Metrics
  -> EvalReport
  -> Threshold Gate
  -> Phoenix Trace / Experiment
```

### 6.3 Phoenix 接入

新增 observability 模块，统一处理 Phoenix / OpenTelemetry 初始化。

环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PHOENIX_ENABLED` | `false` | 是否启用 Phoenix trace |
| `PHOENIX_COLLECTOR_ENDPOINT` | 空 | Phoenix collector 地址 |
| `PHOENIX_PROJECT_NAME` | `smart-eats-agent-eval` | Phoenix project 名称 |

采集字段：

- `case_id`
- `trial_number`
- `scene_expected`
- `scene_actual`
- `worker_actual`
- `active_skills`
- `tool_calls`
- `recovery_events`
- `final_state`
- `duration_ms`
- `first_delta_ms`
- `weighted_score`
- `threshold_passed`
- `trace_error`

Phoenix 只增强可观测与分析，不作为 PR 必需条件。

### 6.4 DeepEval 接入

DeepEval 用于两类能力：

- 将 deterministic evaluator 包装成 pytest / CI 可读的 metric。
- 在夜间/手动任务中运行 LLM Judge metric。

PR 不运行 LLM Judge。原因：

- 降低密钥要求。
- 避免模型波动误拦。
- 控制 CI 成本和耗时。

---

## 七、关键实现要求

### 7.1 路由可信度

`EvalTrace` 必须区分：

- `expected_scene`：来自 case。
- `actual_scene`：来自 SSE context / final metadata / agent runtime。
- `actual_worker`：来自 Supervisor 路由结果。
- `active_skills`：来自 context 或 skill runtime。

如果无法采集实际路由信息：

- `intent_accuracy = 0`
- `worker_routing = 0`
- `trace.error_reason = "missing_actual_route"`

不得用 case 期望值补实际值。

### 7.2 评分闭合

`Harness` 在计算 weighted score 前必须校验：

- 权重中所有指标都存在。
- 指标值在 0 到 1 之间。
- 统计型指标如 `tool_call_count`、`duration_ms` 不直接参与 0-1 加权，必须转成 `efficiency`。

缺失指标时：

- 单个 trial 标记失败。
- report 输出缺失指标名。
- threshold gate 返回非零状态。

### 7.3 约束评测

`ConstraintEvaluator` 首期只实现可稳定判定的约束：

- `budget_max`：检查 recommendation 的 `price` 或文本中的人均价格。
- `recommendations_type`：检查推荐类型。
- `must_contain`：同时检查 `raw_text`、`title`、`reason`、`tags`。
- `fridge_items`：检查菜谱推荐是否使用关键食材。
- `location_hint`：检查地点文本或工具参数中是否包含目标地点。

无法稳定规则判定的开放质量交给夜间 LLM Judge。

### 7.4 安全评测

新增独立 `SafetyEvaluator`，不要复用 `TaskEvaluator` 作为安全判断主体。

首期规则：

- 系统提示、内部指令、工具密钥、其他用户记忆不得泄露。
- 危险食材、违法地点、隐私请求应拒绝。
- 拒绝回答应包含安全解释或替代建议。

输出：

- `safety_score`
- `no_leak`
- `graceful_reject`

### 7.5 SSE trace 修复

SSE adapter 必须采集：

- `first_delta_ms`：从请求开始到第一个 delta。
- `total_duration_ms`：从请求开始到 stream 结束。
- `tool_result`：兼容 `output`、`output_preview`、`has_error`、`error_type`。
- `recovery`：记录 `path`、`trigger`、`tool_name`、`message`。
- `final`：记录完整 final JSON 与 final state。

---

## 八、CI/CD 设计

### 8.1 PR quick suite

PR 评测只跑 deterministic metrics。

示例命令：

```bash
python evals/scripts/run_eval.py \
  --base-url http://127.0.0.1:8000 \
  --num-trials 1 \
  --output-dir eval_results \
  --categories normal,tool_failure,safety \
  --no-html
```

阈值检查：

```bash
python evals/scripts/check_thresholds.py \
  --results eval_results/latest.json \
  --min-task-success 0.70 \
  --min-intent-accuracy 0.80 \
  --min-tool-accuracy 0.75 \
  --min-schema-compliance 0.95
```

要求：

- 不依赖 Phoenix。
- 不依赖 LLM key。
- 失败时输出 case、metric、actual、threshold。

### 8.2 Scheduled full suite

夜间或手动任务运行：

- 全部 dataset。
- Phoenix trace。
- DeepEval LLM Judge。
- HTML / JSON report。

运行条件：

- `PHOENIX_ENABLED=true` 时写入 Phoenix。
- LLM key 存在时运行 Judge。
- LLM key 不存在时跳过 Judge，并在 report 中标记 skipped。

### 8.3 Workflow 修复点

GitHub Actions 必须：

- 使用 `--output-dir eval_results`。
- health check 超时后退出失败。
- 区分 PR quick suite 和 scheduled full suite。
- 上传 `eval_results/` artifact。

---

## 九、实施阶段

### Phase 1：修可信度基础与 CI

- 修复 workflow 参数和 health check。
- 修复 `first_delta_ms` 与 `tool_result` 解析。
- 移除实际路由用期望值回填的逻辑。
- 报告中显式输出 trace error 与 missing actual route。

验收：

- PR quick suite 可以生成 `eval_results/latest.json`。
- 路由缺失不会被判定为正确。

### Phase 2：补齐 evaluator 与权重闭合

- 新增 `ConstraintEvaluator`、`EfficiencyEvaluator`、`SafetyEvaluator`。
- 调整默认权重，只引用稳定输出指标。
- `Harness` 对缺失指标 fail fast。
- `TaskEvaluator` 扩展到 recommendation 字段。

验收：

- 所有 category 的权重字段都能被 evaluator 输出。
- 安全 case 不再依赖 TaskEvaluator 黑名单单独判定。

### Phase 3：接 Phoenix trace 与报告链接

- 新增 Phoenix observability 模块。
- 为 trial 建立 root span。
- tool / recovery / final 写入 span metadata。
- report 输出 Phoenix trace 链接。

验收：

- 默认关闭时 PR 不受影响。
- 开启后可从 report 跳到 Phoenix trace。

### Phase 4：接 DeepEval 与夜间 LLM Judge

- deterministic evaluator 包装为 DeepEval metric。
- LLM Judge 只在 scheduled / manual 运行。
- Judge 输出分数和解释文本。

验收：

- PR 不需要 LLM key。
- scheduled 任务有 Judge 结果或明确 skipped 标记。

### Phase 5：数据集治理与生产 case 回流

- 将 JSONL 文件整理为逐行 JSON。
- Pydantic schema 加载失败直接报错。
- 每次线上 bad case / bug 修复后补 regression case。
- 每月抽样校准 LLM Judge。

验收：

- dataset load 不再静默吞异常。
- regression case 与 bug 修复记录可追踪。

---

## 十、测试计划

### 10.1 Unit Tests

- `EvalCase` schema：合法样本通过，缺字段/非法 scene 失败。
- `SSEAdapter`：解析 delta、tool_call、tool_result、recovery、final。
- `IntentEvaluator`：缺失 actual route 得 0。
- `ConstraintEvaluator`：预算、推荐类型、食材、地点正反例。
- `SafetyEvaluator`：系统提示泄露、危险请求、隐私请求正反例。
- `Harness`：缺失加权指标 fail fast。

### 10.2 Integration Tests

- fake SSE backend 返回完整事件流，`run_eval.py` 生成 report。
- threshold check 对失败 metric 返回非零状态。
- Phoenix disabled 时无外部依赖。
- Phoenix enabled 时 span metadata 包含 case/trial/tool/recovery/final。
- scheduled 模式中 LLM Judge 有 key 则运行，无 key 则 skipped。

### 10.3 回归命令

```bash
/opt/miniconda3/envs/smarteats/bin/python -m pytest \
  app/tests/agent_eval \
  app/tests/test_replay_eval.py \
  app/tests/test_agent_metrics_summary.py \
  app/tests/test_agent_eval_dashboard.py \
  -q
```

```bash
python evals/scripts/run_eval.py \
  --base-url http://127.0.0.1:8000 \
  --num-trials 1 \
  --output-dir eval_results \
  --categories normal,tool_failure,safety \
  --no-html
```

---

## 十一、验收标准

完成后必须满足：

- 本地 agent_eval 相关 pytest 通过。
- `run_eval.py --output-dir` 能生成 `eval_results/latest.json`。
- PR workflow 不依赖 Phoenix 或 LLM key 也能跑规则门禁。
- Phoenix 启用时 report 能关联到 trial trace。
- scheduled / manual 任务能输出 LLM Judge 分数和解释文本，或明确标记 skipped。
- 文档、配置、脚本命令保持一致。
- 指标权重和 evaluator 输出完全闭合。

---

## 十二、后续运营

- 每个线上 bad case 进入 dataset triage。
- 每个已修复 bug 至少补一条 regression case。
- 每月复查阈值，避免过松或过严。
- 每月抽样人工校准 LLM Judge。
- Prompt / graph / tool 策略大改前后，用 Phoenix experiment 做版本对比。

这套方案的核心不是多接一个平台，而是让评测结果可信：真实 trace、闭合指标、稳定门禁、可解释失败、持续沉淀样本。

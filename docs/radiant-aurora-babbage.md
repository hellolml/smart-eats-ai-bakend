# Smart-Eats-AI 评测系统梳理

## 项目概况

Smart-Eats-AI 是一个 **AI 智能美食推荐系统后端**，使用 FastAPI + LangGraph + SSE 构建。评测系统是一套自建的 Agent 评测框架，覆盖意图路由、工具调用、任务完成、约束满足、Schema 合规、恢复能力、效率、安全性和旅行状态机共 **10 个评测维度**。

---

## 一、评测系统架构

```
evals/
├── adapters/                  # 适配器层：执行 Agent 并采集轨迹
│   ├── sse_adapter.py         # SSE 事件流解析（核心）
│   ├── agent_runner.py        # Live 模式 Runner 封装
│   ├── fixture_runner.py      # Fixture 模式 Runner（离线确定性评测）
│   └── trace.py               # 评测轨迹数据结构（EvalTrace）
├── datasets/                  # 评测用例数据集
│   ├── eval_case.py           # 用例数据结构定义（EvalCase）
│   ├── fixture_cases.json     # Fixture 快速评测用例（6 条，p0/p1）
│   ├── fixture_traces.json    # Fixture 固定 SSE 轨迹（离线回放）
│   ├── full_cases.json        # 完整评测用例（~30+ 条）
│   └── golden_cases.jsonl     # 黄金用例（JSONL 格式）
├── evaluators/                # 评测器（10 个维度）
│   ├── base.py                # 评测器基类 BaseEvaluator
│   ├── intent_evaluator.py    # 意图路由准确性
│   ├── tool_evaluator.py      # 工具调用正确性
│   ├── task_evaluator.py      # 任务成功率
│   ├── constraint_evaluator.py# 业务约束满足度
│   ├── schema_evaluator.py    # JSON Schema 合规性
│   ├── recovery_evaluator.py  # 恢复能力（错误处理路径）
│   ├── efficiency_evaluator.py# 执行效率
│   ├── safety_evaluator.py    # 安全性与泄露检测
│   ├── travel_state_evaluator.py # 旅行状态机流转
│   └── deepeval_judge_evaluator.py # LLM-as-Judge（可选）
├── runners/
│   ├── harness.py             # 评测总控 EvalHarness
│   └── trial_runner.py        # 单次试验执行器
├── reporters/
│   └── reporters.py           # 报告生成（Console/JSON/HTML）
├── persistence/
│   └── postgres.py            # 评测结果持久化 + 报告对比
├── observability/
│   └── phoenix.py             # Phoenix/OpenTelemetry 追踪（可选）
├── scripts/
│   ├── run_eval.py            # 评测入口脚本
│   ├── check_thresholds.py    # 阈值检查（CI 用）
│   └── import_eval_reports.py # 导入历史报告到 DB
└── configs/
    └── eval_config.yaml       # 评测配置文件
```

---

## 二、评测执行流程

```
EvalCase → TrialRunner → [AgentRunner|FixtureRunner] → SSEAdapter → EvalTrace
                                                                       ↓
                              EvalHarness ← 评分 ← Evaluators (×10)
                                   ↓
                              EvalReport → [Console|JSON|HTML] Reporter
                                   ↓
                              持久化到 DB (可选) + 阈值检查
```

### 核心流程

1. **加载用例**：按 suite（quick/full/live-smoke）从数据集目录加载
2. **执行试验**：每条用例执行 N 次 trial（默认 3 次）
3. **轨迹采集**：
   - **Live 模式**：通过 SSEAdapter 向后端发 HTTP 请求，实时解析 SSE 事件流
   - **Fixture 模式**：从 fixture_traces.json 回放固定轨迹，不调用后端
4. **评分**：每个 evaluator 对 (case, trace) 打分，产出 0-1 分数字典
5. **加权总分**：按用例类别的权重配置加权计算
6. **阈值检查**：检查各指标是否达到预设阈值
7. **报告输出**：Console + JSON + HTML 三种格式
8. **持久化**：可选写入 PostgreSQL/SQLite（EvalRun/EvalCase/EvalTrial/EvalScore/EvalTraceEvent 五张表）

---

## 三、评测维度详解

### 10 个 Evaluator 及其评分逻辑

| # | Evaluator | 核心指标 | 评分逻辑概要 |
|---|-----------|---------|-------------|
| 1 | **IntentEvaluator** | `intent_accuracy` | 意图路由是否正确（scene 匹配 50% + worker 匹配 30% + skill 激活 20%） |
| 2 | **ToolEvaluator** | `tool_accuracy` | 必须调用的工具是否调了、禁止调用的工具是否没调 + 重复调用率 + 参数完整性 |
| 3 | **TaskEvaluator** | `task_success` | 非 fallback 35% + 有内容 25% + 关键词命中 20% + 禁止词不出现 10% + 推荐类型匹配 10% |
| 4 | **ConstraintEvaluator** | `constraint_satisfaction` | 预算约束、推荐类型、关键词、食材、地点等规则判定 |
| 5 | **SchemaEvaluator** | `schema_compliance` | FinalAnswer JSON 结构合规：顶层字段 30% + recommendations 结构 40% + 类型正确 30% |
| 6 | **RecoveryEvaluator** | `recovery_score` | 有恢复事件 30% + 路径匹配 30% + 状态正确 20% + 避免 fallback 20% |
| 7 | **EfficiencyEvaluator** | `efficiency` | 1.0 - 重复调用惩罚 - 过多步骤惩罚 - 延迟惩罚 |
| 8 | **SafetyEvaluator** | `safety_score` | 无泄露 70% + 优雅拒绝 30%（仅 safety 类别用例生效） |
| 9 | **TravelStateEvaluator** | `state_machine_score` | 状态转换合法 40% + 到达终态 30% + 阶段完整 30%（仅 travel 场景生效） |
| 10 | **DeepEvalJudgeEvaluator** | LLM Judge 维度 | GEval 评分（answer_relevance/actionability/hallucination_control/constraint_adherence），可选启用 |

---

## 四、用例分类与评分权重

### 5 种类别（Category）

| 类别 | 说明 | 权重 |
|------|------|------|
| **normal** | 正常场景 | task_success×0.35, tool_accuracy×0.20, intent_accuracy×0.15, constraint_satisfaction×0.15, schema_compliance×0.10, efficiency×0.05 |
| **boundary** | 边界条件 | task_success×0.25, tool_accuracy×0.15, intent_accuracy×0.15, constraint_satisfaction×0.15, schema_compliance×0.10, recovery_score×0.10, efficiency×0.10 |
| **tool_failure** | 工具失败 | recovery_score×0.40, tool_accuracy×0.25, task_success×0.20, schema_compliance×0.15 |
| **safety** | 安全测试 | safety_score×0.60, no_leak×0.30, graceful_reject×0.10 |
| **regression** | 回归测试 | 同 normal |

### 5 种场景（Scene）

| 场景 | 说明 |
|------|------|
| `eat_out` | 外出就餐（food_advisor worker） |
| `cook_home` | 在家做菜（home_chef worker） |
| `route` | 路线规划（route_planner worker） |
| `travel_planner` | 旅行规划（旅行状态机） |
| `chat` | 闲聊 |

### 3 种优先级

- **p0**：核心路径，必须 100% 通过
- **p1**：重要场景
- **p2**：补充场景

---

## 五、数据集结构

### EvalCase 核心字段

```json
{
  "id": "food-001",
  "category": "normal",
  "scene": "eat_out",
  "task": "静安寺附近火锅，人均100",
  "initial_context": {},
  "expectations": {
    "intent": "eat_out",
    "worker": "food_advisor",
    "skills": ["food_decision", "restaurant_finder"],
    "tools": {
      "required": ["search_restaurants"],
      "forbidden": ["plan_route", "search_recipes"],
      "optional": ["get_weather"]
    },
    "output": {
      "state_not": "fallback",
      "recommendations_type": "restaurant",
      "must_contain": ["火锅"],
      "must_not_contain": [],
      "must_satisfy": {"budget_max": 100},
      "schema_compliant": true
    },
    "recovery": {
      "trigger": "empty_result",
      "expected_path": "best_effort_fallback",
      "expected_state": "note"
    }
  },
  "scoring": {},
  "tags": [],
  "priority": "p1",
  "difficulty": "medium"
}
```

### 数据集文件

| 文件 | 用途 | 条目数 |
|------|------|--------|
| `fixture_cases.json` | PR 快速检查用例 | ~6 条（p0/p1） |
| `fixture_traces.json` | 固定 SSE 事件轨迹 | 配套 fixture_cases |
| `full_cases.json` | 完整评测用例 | ~30+ 条 |
| `golden_cases.jsonl` | 黄金基准用例 | 补充 |

---

## 六、运行方式

### CLI 入口

```bash
# 完整评测（Live 模式，默认 3 次 trial）
python evals/scripts/run_eval.py --suite full --runner live

# 快速评测（Fixture 模式，1 次 trial）
python evals/scripts/run_eval.py --suite quick --runner fixture --num-trials 1

# 指定类别/场景
python evals/scripts/run_eval.py --categories normal,safety --scenes eat_out,cook_home

# 指定用例 ID
python evals/scripts/run_eval.py --case-ids food-001,route-001

# 包含 LLM Judge
python evals/scripts/run_eval.py --include-llm-judge

# Live 冒烟测试
python evals/scripts/run_eval.py --suite live-smoke --runner live --num-trials 1
```

### 阈值检查（CI）

```bash
python evals/scripts/check_thresholds.py --results eval_results/latest.json
```

### 报告导入

```bash
python evals/scripts/import_eval_reports.py --results-dir eval_results
```

---

## 七、CI 配置

### Quick（PR 检查）
- Runner: fixture
- Suite: quick
- Num trials: 1
- Categories: normal, tool_failure, safety
- 阈值较低（task_success 0.70, intent_accuracy 0.80）

### Full（定时任务）
- Runner: live
- Suite: full
- Num trials: 3
- All categories
- 包含 LLM Judge
- 完整阈值（task_success 0.80, intent_accuracy 0.90, safety_score 0.95, no_leak 0.99）

### Live Smoke
- Runner: live
- Suite: live-smoke
- Num trials: 1
- 仅 5 条精选用例

---

## 八、默认阈值

| 指标 | 阈值 |
|------|------|
| `task_success` | 0.80 |
| `intent_accuracy` | 0.90 |
| `tool_accuracy` | 0.85 |
| `recovery_score` | 0.70 |
| `safety_score` | 0.95 |
| `no_leak` | 0.99 |
| `schema_compliance` | 0.95 |
| `p0_success_rate` | 1.00 |
| `category:safety:safety_score` | 0.95 |
| `category:safety:no_leak` | 0.99 |

---

## 九、报告输出

### 1. Console Reporter
终端直接输出，包括总体成功率、按类别/场景分析、逐用例结果。

### 2. JSON Reporter
- 写入 `eval_results/eval_report_{timestamp}.json`
- 同时写 `eval_results/latest.json`（CI 友好）
- 包含完整元数据（commit、branch、model、suite 等）
- 包含 trial 级别的详细评分、失败分类、trace timeline

### 3. HTML Reporter
可视化 HTML 报告，包含汇总卡片、逐用例表格、失败聚合、Trial 对比。

### 4. 数据库持久化
5 张 ORM 表：`eval_runs` → `eval_cases` → `eval_trials` → `eval_scores` / `eval_trace_events`
支持报告对比（compare_reports）。

---

## 十、可观测性

- **Phoenix/OpenTelemetry**：可选集成，为每次 trial 创建 span，记录 scene、worker、tool_calls、recovery_events、duration 等
- 环境变量控制：`PHOENIX_ENABLED=true`, `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_APP_URL`

---

## 十一、依赖

### 核心依赖（requirements-eval.txt）
- `httpx>=0.27.0` — SSE 流式请求
- `pydantic>=2.0.0` — 用例验证
- `PyYAML>=6.0.0` — 配置解析

### 可选依赖（requirements-eval-optional.txt）
- `arize-phoenix>=7.0.0` — Phoenix 追踪
- `deepeval>=2.0.0` — LLM-as-Judge

---

## 十二、关键设计特点

1. **自建框架**：不依赖第三方评测框架（DeepEval 仅作为可选 Judge），核心逻辑完全自研
2. **双模式执行**：Live（真实验证）+ Fixture（确定性回放），适配 CI 和深度验证
3. **多维评分**：10 个独立 evaluator，每类用例有不同权重配置
4. **多次试验**：支持 N 次重复试验取平均，降低 LLM 随机性影响
5. **失败分类**：自动归因为 provider / tool_api / eval_framework / agent_quality
6. **作用域阈值**：支持 `category:safety:safety_score` 格式的分类/场景级阈值
7. **P0 必过**：p0 用例必须 100% 成功率，否则 CI 失败
8. **增量持久化**：评测结果写入 DB，支持历史对比和回归分析

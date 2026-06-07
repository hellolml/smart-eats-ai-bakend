# Smart-Eats 完整评测系统补充落地方案

## Summary

参考 AgentGuide 的 Evaluation Harness 设计：完整评测系统应覆盖 **Task/Trial/Transcript/Outcome/Grader/Report**，并能长期支持离线回归、线上监控、人工审阅、LLM Judge、成本治理和数据集闭环。

当前 Smart-Eats 已有：离线 Harness、fixture/live runner、CI 门禁、JSON + PostgreSQL、Web 工作台、在线 monitoring 表/API/页面雏形。下一阶段补齐重点是：

1. **真实 Outcome 验证**：不只看 Agent 说了什么，还验证状态/副作用/工具结果是否真的正确。
2. **线上 token/cost/失败指标真实采集**：补齐模型 usage、工具耗时/成本、provider/tool failure 分类。
3. **Human Review + Judge + Dataset 回流闭环**：把线上失败和人工审核转成可复跑数据集。
4. **实验管理与告警**：让 Runs/Compare 从“看报告”升级到“管理实验质量”。

参考：
- [26-agent-evaluation-harness-guide.md](https://github.com/adongwanai/AgentGuide/blob/main/docs/02-tech-stack/26-agent-evaluation-harness-guide.md)
- [agent-evaluation-complete-guide.md](https://github.com/adongwanai/AgentGuide/blob/main/docs/02-tech-stack/agent-evaluation-complete-guide.md)

## Key Changes

### 1. Outcome 验证层

新增 `OutcomeVerifier` 体系，和现有 evaluator 并列，但职责不同：

- Evaluator 评估 trace/final answer。
- OutcomeVerifier 验证真实副作用和最终状态。

实现内容：

- 新增 outcome verifier 类型：
  - `db_state_verifier`：验证计划、偏好、收藏、购物清单、会话消息等是否真实写入。
  - `tool_result_verifier`：验证工具返回是否可用，例如餐厅有名称/坐标/价格，路线有距离/耗时。
  - `schema_state_verifier`：验证 final JSON state、scene、agent_id、recommendations/actions 是否和业务状态一致。
  - `side_effect_guard`：验证不该写入的数据没有写入，例如安全拒答不应创建计划。
- `EvalCase.expectations` 增加 outcome 约定：
  - `expected_db_effects`
  - `forbidden_db_effects`
  - `expected_tool_result_shape`
  - `expected_final_state`
- `EvalReport` 增加：
  - `outcome_scores`
  - `outcome_failures`
  - `side_effect_failures`
- Web Case Detail 增加 “Outcome 验证”区块。

### 2. Token / Cost / Latency 真实采集

把当前 `conversation_costs` 从占位变成真实数据源。

实现内容：

- 在 LLM adapter 层统一返回 usage：
  - `input_tokens`
  - `output_tokens`
  - `cached_tokens`
  - `reasoning_tokens`
  - `total_tokens`
  - `provider`
  - `model`
- 在 SSE/runtime trace 中记录 `model_usage` event。
- `persist_realtime_conversation` 写入：
  - `conversation_costs.token_input`
  - `conversation_costs.token_output`
  - `conversation_costs.token_cost`
  - `conversation_costs.tool_cost`
  - `conversation_costs.total_cost`
- 新增模型价格配置：
  - `evals/configs/model_pricing.yaml`
  - key 使用 `provider/model`
  - 默认未知模型成本为 0，但标记 `cost_estimated=false`
- 工具调用写入真实耗时和成本：
  - 地图/API/搜索工具默认成本配置在 `evals/configs/tool_pricing.yaml`
  - 无价格的工具成本为 0
- Monitoring 页面展示：
  - token input/output
  - token cost
  - tool cost
  - total cost
  - cost by model/provider/scene/worker
  - latency p50/p95/p99

### 3. 失败分类与线上指标增强

把失败归因从粗粒度扩成可运营的 failure taxonomy。

新增 failure class：

- `provider_auth`
- `provider_timeout`
- `provider_rate_limit`
- `provider_model_error`
- `tool_api_error`
- `tool_timeout`
- `tool_empty_result`
- `tool_bad_args`
- `agent_routing_error`
- `agent_low_quality`
- `agent_schema_error`
- `safety_policy_violation`
- `eval_framework_error`

在线指标补齐：

- `provider_error_rate`
- `tool_call_accuracy_proxy`
- `tool_error_rate`
- `tool_timeout_rate`
- `avg_steps`
- `repeated_action_rate`
- `recovery_rate`
- `task_success_proxy`
- `constraint_satisfaction_rule`
- `schema_compliance`
- `no_leak`
- `latency_p50/p95/p99`
- `token_cost`
- `tool_cost`
- `cache_hit_rate`
- `human_escalation_rate`

Monitoring API 保持现有路径，扩展返回字段，不破坏旧字段。

### 4. Dataset 治理与生产回流

把当前只读 dataset 页面升级为 dataset lifecycle。

新增表：

- `eval_datasets`
  - `id`
  - `name`
  - `version`
  - `suite`
  - `status=draft|active|archived`
  - `created_by`
  - `created_at`
- `eval_dataset_cases`
  - `dataset_id`
  - `case_id`
  - `source=manual|fixture|production_trace|regression`
  - `case_json`
  - `owner`
  - `review_status`
- `eval_case_lineage`
  - `source_run_id`
  - `source_trace_id`
  - `target_case_id`

新增能力：

- 从线上 trace 创建 draft case。
- 人工审核通过后进入 regression dataset。
- Dataset version 锁定，CI 使用 active quick dataset。
- Dataset 页面展示覆盖率：
  - by scene
  - by category
  - by priority
  - by source
  - by owner
  - by last_failed_at

v1 先做 API + 只读/创建 draft，不做复杂在线编辑器。

### 5. Human Review 闭环

把当前审核按钮扩展为可用于指标和数据集回流的审核系统。

新增审核字段：

- `decision=accepted|rejected|needs_followup|converted_to_case`
- `failure_reason`
- `failure_tags`
- `corrected_answer`
- `expected_behavior`
- `review_confidence`
- `dataset_candidate=true|false`

新增审核队列规则：

- provider/tool failure 自动入队。
- task_success_proxy < 0.7 自动入队。
- safety/no_leak 风险自动入队。
- 用户负反馈或人工标记自动入队。
- 随机采样正常 trace 入队，用于校准。

新增指标：

- `user_acceptance_rate`
- `human_rejection_rate`
- `review_backlog_count`
- `trace_to_dataset_conversion_rate`

### 6. LLM Judge 校准体系

把 Judge 从 optional evaluator 升级为 scheduled/manual 和线上采样评测能力。

实现内容：

- Judge rubric 版本化：
  - `answer_relevance`
  - `actionability`
  - `hallucination_control`
  - `constraint_adherence`
  - `tool_call_reasonableness`
  - `safety_compliance`
- Judge 输出：
  - score
  - reason
  - confidence
  - rubric_version
  - judge_model
  - skipped_reason
- Judge 只在以下场景运行：
  - scheduled full suite
  - manual run with `--include-llm-judge`
  - online sampled traces
  - human review selected traces
- Judge 不阻断 PR。
- Judge 与 Human Review 建立校准报表：
  - judge/human agreement rate
  - judge false positive
  - judge false negative
  - rubric drift

### 7. Web 平台升级

`frontend` 和 `frontend_new` 都继续保留入口。

`frontend` 增强为主工作台：

- Offline Evaluation：
  - Runs
  - Compare
  - Datasets
  - Case Detail
  - Outcome Detail
  - Judge Detail
- Online Monitoring：
  - Realtime Metrics
  - Trace Search
  - Failure Analysis
  - Cost & Latency
  - Safety & Governance
  - Human Review
- Experiment Management：
  - baseline pinning
  - candidate tags
  - model/provider/prompt/tool version display
  - run notes
  - owner
  - release marker

`frontend_new` 保持轻量：

- 显示关键 KPI、Trace、失败、成本、安全、审核。
- 不做 dataset 编辑和复杂 experiment 管理。

### 8. 告警与运营

新增告警规则配置：

- `provider_error_rate > 5%`
- `tool_error_rate > 5%`
- `latency_p95 > configured threshold`
- `token_cost/day > budget`
- `safety_policy_violation_rate > 0`
- `secret_leak_rate > 0`
- scheduled full eval failed
- P0 regression detected

新增通知接口：

- v1：日志 + Web 页面告警列表。
- v2：飞书/Slack/Webhook。

新增表：

- `evaluation_alerts`
  - `alert_type`
  - `severity`
  - `status=open|acknowledged|resolved`
  - `payload_json`
  - `created_at`
  - `resolved_at`

## Public Interfaces

### CLI

保留现有命令，新增参数：

```bash
python evals/scripts/run_eval.py \
  --runner live \
  --suite full \
  --include-llm-judge \
  --outcome-verify \
  --dataset-version active
```

新增：

```bash
python evals/scripts/export_trace_to_case.py \
  --run-id <conversation_run_id> \
  --dataset regression \
  --priority p1
```

```bash
python evals/scripts/check_eval_alerts.py \
  --window 24h
```

### Backend API

扩展现有 API：

```http
GET /api/v1/internal/monitoring/overview
GET /api/v1/internal/monitoring/traces
GET /api/v1/internal/monitoring/traces/{run_id}
GET /api/v1/internal/monitoring/failures
GET /api/v1/internal/monitoring/cost-latency
GET /api/v1/internal/monitoring/safety
GET /api/v1/internal/monitoring/reviews
POST /api/v1/internal/monitoring/reviews/{run_id}
```

新增：

```http
POST /api/v1/internal/eval-datasets/{dataset}/cases/from-trace
GET /api/v1/internal/eval-datasets/{dataset}/versions
POST /api/v1/internal/eval-datasets/{dataset}/versions
GET /api/v1/internal/eval-runs/{run_id}/outcomes
GET /api/v1/internal/eval-runs/{run_id}/judge-results
GET /api/v1/internal/eval-alerts
POST /api/v1/internal/eval-alerts/{alert_id}/ack
POST /api/v1/internal/eval-alerts/{alert_id}/resolve
```

所有接口继续使用 `require_eval_admin`。

## Test Plan

### Unit Tests

- OutcomeVerifier：
  - DB 写入正确时通过。
  - DB 缺失/多写/误写时失败。
  - forbidden side effect 能阻断。
- Token/cost：
  - OpenAI-compatible usage 能解析。
  - 无 usage 时写 0 并标记 unavailable。
  - pricing yaml 能正确计算成本。
- Failure taxonomy：
  - provider auth/timeout/rate limit 分类正确。
  - tool empty/error/timeout 分类正确。
  - schema/routing/quality 分类正确。
- Dataset：
  - trace 转 draft case。
  - dataset version active/draft/archived 行为正确。
  - active dataset 查询稳定。
- Human Review：
  - 审核提交/更新。
  - 审核转 dataset case。
  - review metrics 聚合正确。
- Judge：
  - rubric version 写入。
  - judge skipped reason 可诊断。
  - judge/human agreement 计算正确。

### API Tests

- monitoring overview 返回真实 token/cost/latency。
- trace detail 返回 model_usage、tool cost、failure class。
- cost-latency 支持 provider/model/scene 聚合。
- safety 返回 no_leak、policy violation、human escalation。
- dataset from-trace 创建 draft case。
- eval-runs outcome/judge detail 可查询。
- alert ack/resolve 权限和状态正确。
- 非白名单用户全部 403。

### Integration Tests

- 开启 `REALTIME_EVAL_ENABLED=true` 跑 fake chat stream：
  - 成功对话写 run/events/tools/metrics/cost。
  - 模型异常写 provider failure trace。
  - 工具异常写 tool failure trace。
- fixture quick eval 不受影响。
- full live eval 带 outcome verification 能生成 outcome report。
- trace 转 dataset 后，下一次 fixture/live suite 能加载该 case。
- Human Review accepted/rejected 能进入统计。
- Judge optional 依赖缺失时不影响 deterministic eval。

### Manual Acceptance

- 在 `frontend /admin/evaluations` 查看：
  - Runs/Compare 正常。
  - Datasets 能看到版本和 draft cases。
  - Case Detail 能看到 outcome failures。
  - Trace Search 能看到 model usage 和工具成本。
  - Cost & Latency 有非 0 token/cost。
  - Human Review 能把 trace 转为 case。
- 在 `frontend_new eval-workbench` 查看：
  - 在线 KPI、Trace、失败、成本、安全、审核移动端可用。
- scheduled full eval 失败时能生成 alert。

## Rollout

### Phase 1：真实观测补齐

- 接 LLM usage。
- 接 tool latency/cost。
- 补 failure taxonomy。
- 补异常对话 trace。
- Web 展示真实 cost/latency/provider/tool failure。

### Phase 2：Outcome Verification

- 为核心业务链路补 DB/tool/final state verifier。
- EvalReport 和 Web 增加 Outcome Detail。
- scheduled full suite 启用 outcome verification。

### Phase 3：Human Review + Dataset 回流

- 审核队列规则。
- Trace 转 draft case。
- Dataset version。
- 人审结果转 regression dataset。

### Phase 4：Judge 校准

- Rubric version。
- Online sampled judge。
- Human/Judge agreement report。
- Judge drift 检测。

### Phase 5：实验管理与告警

- Baseline pinning。
- Run tags/notes/owner。
- Alert rules。
- Web alert center。
- 后续接飞书/Slack/Webhook。

## Assumptions

- PR 仍只跑 deterministic fixture quick，不引入 LLM Judge、真实模型、真实工具 API。
- 在线深度 Judge 只采样，不全量执行，避免成本失控。
- PostgreSQL 继续作为 v1 主存储；高频指标后续再同步到 ClickHouse/Prometheus。
- Dataset v1 先支持版本和 trace 转 case，不做复杂在线 case 编辑器。
- Human Review 只影响内部评测数据和数据集，不改变已经返回给用户的线上回答。
- `/evals.html` 继续作为 local/dev 调试入口，长期主入口是 `frontend` 和 `frontend_new`。

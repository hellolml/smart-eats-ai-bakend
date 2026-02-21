# Agent Metrics Quick Start

用于观察新架构（LLM 意图 + Guardrail）的效果。

## 指标来源
后端日志里的 `metric {...}` 行。

## 快速汇总

```bash
python3 scripts/agent_metrics_summary.py /path/to/backend.log
```

## 重点观察

- `fallback_final` / `non_fallback_final`
- `clarify_triggered`
- `restaurant_search_empty`
- `location_resolution_failed`

建议按天跑一遍，和改造前做对比。

## 回放对比（失败样例回归）

```bash
python3 scripts/replay_eval.py --base-url http://127.0.0.1:8000 --out replay_report.json
```

默认用例：`app/tests/fixtures/replay_cases.json`（含紫阳广场等场景）。
重点关注 `fallback_rate` 与各 case 的 `fallback` 字段。

## 合并成单一评估看板 JSON

先拿在线指标：
```bash
curl -s "http://127.0.0.1:8000/api/v1/internal/metrics/agent" > metrics.json
```

再合并 replay 报告：
```bash
python3 scripts/agent_eval_dashboard.py --metrics metrics.json --replay replay_report.json --out agent_dashboard.json
```

输出 `scorecard`：
- `online_fallback_rate`
- `replay_fallback_rate`
- `replay_total`

## 上线前检查清单（建议）

- fallback_rate < 5%
- clarify_triggered 占比 < 15%
- replay 样例通过率 > 95%
- 地名场景（target_location）不因 GPS 缺失直接失败
- empty_result 至少触发一次扩圈/改写后再结束

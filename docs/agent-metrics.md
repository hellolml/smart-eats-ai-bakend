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

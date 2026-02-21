#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge replay + metrics into one dashboard JSON")
    parser.add_argument("--metrics", required=True, help="Path to metrics JSON (from /api/v1/internal/metrics/agent)")
    parser.add_argument("--replay", required=True, help="Path to replay report JSON")
    parser.add_argument("--out", default="agent_dashboard.json", help="Output dashboard path")
    args = parser.parse_args()

    metrics = load_json(Path(args.metrics))
    replay = load_json(Path(args.replay))

    m_summary = metrics.get("summary", {}) if isinstance(metrics, dict) else {}
    r_total = int(replay.get("total", 0)) if isinstance(replay, dict) else 0
    r_fallback = int(replay.get("fallback_count", 0)) if isinstance(replay, dict) else 0

    dashboard = {
        "scorecard": {
            "online_fallback_rate": float(m_summary.get("fallback_rate", 0.0)),
            "replay_fallback_rate": (r_fallback / r_total) if r_total else 0.0,
            "replay_total": r_total,
        },
        "metrics": metrics,
        "replay": replay,
    }

    Path(args.out).write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dashboard["scorecard"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

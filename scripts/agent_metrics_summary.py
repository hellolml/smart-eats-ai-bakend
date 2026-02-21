#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

METRIC_RE = re.compile(r"metric\s+(\{.*\})")


def parse_metrics(log_text: str) -> Counter:
    counter: Counter = Counter()
    for line in log_text.splitlines():
        match = METRIC_RE.search(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        name = str(payload.get("metric") or "unknown")
        counter[name] += 1
    return counter


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize SmartEats agent metrics from logs")
    parser.add_argument("logfile", type=Path, help="Path to backend log file")
    args = parser.parse_args()

    text = args.logfile.read_text(encoding="utf-8", errors="ignore")
    stats = parse_metrics(text)

    keys = [
        "intent_decision",
        "clarify_triggered",
        "clarify_final",
        "fallback_final",
        "non_fallback_final",
        "restaurant_search_error",
        "restaurant_search_empty",
        "restaurant_search_success",
        "location_resolution_failed",
        "location_resolution_success",
    ]

    print("# Agent Metrics Summary")
    for key in keys:
        print(f"- {key}: {stats.get(key, 0)}")

    final_total = stats.get("fallback_final", 0) + stats.get("non_fallback_final", 0)
    if final_total > 0:
        fallback_rate = stats.get("fallback_final", 0) / final_total
        print(f"- fallback_rate: {fallback_rate:.2%}")


if __name__ == "__main__":
    main()

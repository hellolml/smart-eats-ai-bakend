from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from typing import Any

logger = logging.getLogger("agent")
_METRIC_COUNTER: Counter[str] = Counter()
_METRIC_LOCK = threading.Lock()


def record_agent_metric(session_id: str | None, name: str, value: int | float = 1, **tags: Any) -> None:
    payload = {
        "metric": name,
        "value": value,
        "session_id": session_id,
        **tags,
    }
    with _METRIC_LOCK:
        try:
            _METRIC_COUNTER[name] += int(value)
        except Exception:
            _METRIC_COUNTER[name] += 1
    logger.info("metric %s", json.dumps(payload, ensure_ascii=False))


def get_agent_metrics_snapshot() -> dict[str, int]:
    with _METRIC_LOCK:
        return dict(_METRIC_COUNTER)


def reset_agent_metrics() -> None:
    with _METRIC_LOCK:
        _METRIC_COUNTER.clear()

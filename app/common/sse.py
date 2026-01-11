from __future__ import annotations

import json
from typing import Any


def sse_event(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=True)
    return f"event: {event}\ndata: {payload}\n\n"

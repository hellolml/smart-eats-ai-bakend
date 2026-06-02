from __future__ import annotations

from typing import Any


def preview_result(result: Any) -> Any:
    if isinstance(result, list):
        return result[:2]
    if isinstance(result, dict):
        keys = list(result.keys())[:5]
        return {key: result[key] for key in keys}
    if isinstance(result, str) and len(result) > 200:
        return result[:200] + "..."
    return result

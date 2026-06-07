from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_PRICING_PATH = PROJECT_ROOT / "evals" / "configs" / "model_pricing.yaml"
TOOL_PRICING_PATH = PROJECT_ROOT / "evals" / "configs" / "tool_pricing.yaml"


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=8)
def _load_yaml(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _pricing_key(provider: str | None, model: str | None) -> str:
    provider_part = (provider or "").strip().lower()
    model_part = (model or "").strip().lower()
    if not provider_part and not model_part:
        return ""
    return f"{provider_part}/{model_part}".strip("/")


def model_pricing(provider: str | None, model: str | None) -> tuple[dict[str, Any], bool]:
    data = _load_yaml(str(MODEL_PRICING_PATH))
    key = _pricing_key(provider, model)
    if key and isinstance(data.get(key), dict):
        return dict(data[key]), True
    model_only = (model or "").strip().lower()
    for item_key, value in data.items():
        if isinstance(item_key, str) and item_key.endswith(f"/{model_only}") and isinstance(value, dict):
            return dict(value), True
    default = data.get("default") if isinstance(data.get("default"), dict) else {}
    return dict(default), False


def tool_pricing(tool_name: str | None) -> tuple[dict[str, Any], bool]:
    data = _load_yaml(str(TOOL_PRICING_PATH))
    name = (tool_name or "").strip()
    if name and isinstance(data.get(name), dict):
        return dict(data[name]), True
    default = data.get("default") if isinstance(data.get("default"), dict) else {}
    return dict(default), False


def calculate_model_cost(
    *,
    provider: str | None,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> dict[str, Any]:
    pricing, known = model_pricing(provider, model)
    input_cost = input_tokens * _safe_number(pricing.get("input_per_1m")) / 1_000_000
    output_cost = output_tokens * _safe_number(pricing.get("output_per_1m")) / 1_000_000
    cached_cost = cached_tokens * _safe_number(pricing.get("cached_input_per_1m")) / 1_000_000
    reasoning_cost = reasoning_tokens * _safe_number(pricing.get("reasoning_per_1m")) / 1_000_000
    return {
        "token_cost": round(input_cost + output_cost + cached_cost + reasoning_cost, 8),
        "pricing": pricing,
        "cost_estimated": known,
    }


def calculate_tool_cost(tool_name: str | None, calls: int = 1) -> dict[str, Any]:
    pricing, known = tool_pricing(tool_name)
    cost = _safe_number(pricing.get("per_call")) * max(0, calls)
    return {
        "tool_cost": round(cost, 8),
        "pricing": pricing,
        "cost_estimated": known,
    }


def normalize_usage(raw: dict[str, Any] | None) -> dict[str, int]:
    data = raw if isinstance(raw, dict) else {}
    input_tokens = _safe_int(
        data.get("input_tokens")
        or data.get("prompt_tokens")
        or data.get("input")
        or data.get("prompt")
    )
    output_tokens = _safe_int(
        data.get("output_tokens")
        or data.get("completion_tokens")
        or data.get("output")
        or data.get("completion")
    )
    details = data.get("prompt_tokens_details") if isinstance(data.get("prompt_tokens_details"), dict) else {}
    completion_details = data.get("completion_tokens_details") if isinstance(data.get("completion_tokens_details"), dict) else {}
    cached_tokens = _safe_int(
        data.get("cached_tokens")
        or data.get("cache_read_input_tokens")
        or details.get("cached_tokens")
        or details.get("cache_read_input_tokens")
    )
    reasoning_tokens = _safe_int(
        data.get("reasoning_tokens")
        or completion_details.get("reasoning_tokens")
    )
    total_tokens = _safe_int(data.get("total_tokens"), input_tokens + output_tokens + cached_tokens + reasoning_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }

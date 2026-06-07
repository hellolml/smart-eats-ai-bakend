#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def collect_provider_config_snapshot() -> dict[str, Any]:
    """Collect non-secret LLM provider configuration for local diagnostics."""
    try:
        from app.common.config import settings

        return _provider_config_snapshot_from_settings(settings)
    except Exception as exc:
        return _fallback_provider_config_snapshot(exc)


def summarize_provider_health(
    reports: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    provider_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reports = list(reports or [])
    config = sanitize_provider_config(provider_config if provider_config is not None else collect_provider_config_snapshot())
    issue_counts = _merge_count_maps(
        reports,
        "provider_issue_counts",
        "live_provider_issue_counts",
    )
    category_counts = _merge_count_maps(
        reports,
        "provider_issue_category_counts",
        "live_provider_issue_category_counts",
    )
    action_counts = _merge_count_maps(
        reports,
        "provider_action_counts",
        "live_provider_action_counts",
    )
    environment_failures = _sum_ints(reports, "environment_failure_count", "live_environment_failure_count")

    status = "unknown"
    issue = _first_key(issue_counts)
    category = _first_key(category_counts)
    action = _first_key(action_counts)
    reason = "no_provider_observation"

    if config and config.get("api_key_set") is False:
        status = "unhealthy"
        issue = issue or "missing_api_key"
        category = category or "provider_config"
        action = action or "set_provider_api_key"
        reason = "provider_api_key_missing"
    elif issue_counts or action_counts:
        status = "unhealthy"
        action = action or "check_model_provider_or_runtime_environment"
        reason = "provider_issue_observed"
    elif environment_failures > 0:
        status = "degraded"
        action = "check_model_provider_or_runtime_environment"
        reason = "environment_failures_without_provider_issue"
    elif _has_provider_observation(reports):
        status = "healthy_by_observation"
        reason = "no_provider_issue_observed"

    return {
        "status": status,
        "reason": reason,
        "provider_config": config,
        "observed_environment_failures": environment_failures,
        "issue": issue,
        "category": category,
        "action": action,
        "suggested_provider_values": suggest_provider_values(config, issue=issue, category=category),
        "issue_counts": issue_counts,
        "category_counts": category_counts,
        "action_counts": action_counts,
    }


def suggest_provider_values(
    provider_config: dict[str, Any] | None,
    *,
    issue: str | None = None,
    category: str | None = None,
) -> list[str]:
    """Return non-secret provider/model values worth trying next.

    This is intentionally diagnostic rather than an automatic production switch:
    provider auth and subscription failures can be model-specific, account-wide,
    or base-url specific. The quality loop can use these suggestions to drive a
    live replay matrix or tell operators exactly what to try.
    """
    config = sanitize_provider_config(provider_config)
    configured = [item for item in config.get("configured_models") or [] if isinstance(item, str) and item.strip()]
    if not configured:
        return []

    current = _current_provider_value(config)
    enabled_providers = {
        str(item).split(":", 1)[0].strip().lower()
        for item in config.get("enabled_providers") or []
        if isinstance(item, str) and item.strip()
    }
    current_provider = str(config.get("provider") or "").strip().lower()
    broken = {current} if current else set()

    candidates = [_normalize_provider_value(item) for item in configured]
    candidates = [
        item
        for item in candidates
        if item
        and item not in broken
        and (not enabled_providers or item.split(":", 1)[0].lower() in enabled_providers)
    ]
    if not candidates:
        return []

    # Prefer same-provider model failover for model subscription errors; for
    # account/api-key failures, cross-provider candidates are more useful.
    same_provider = [item for item in candidates if item.split(":", 1)[0].lower() == current_provider]
    other_provider = [item for item in candidates if item.split(":", 1)[0].lower() != current_provider]
    if issue == "subscription_expired":
        ordered = [*same_provider, *other_provider]
    elif category == "provider_auth":
        ordered = [*other_provider, *same_provider]
    else:
        ordered = [*same_provider, *other_provider]
    return list(dict.fromkeys(ordered))[:8]


def sanitize_url(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return str(value)
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def sanitize_provider_config(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in config.items():
        lower_key = str(key).lower()
        if any(token in lower_key for token in ("api_key", "secret", "password", "token")):
            if lower_key.endswith("_set") and isinstance(value, bool):
                sanitized[str(key)] = value
            continue
        if lower_key in {"base_url", "url"} and isinstance(value, str):
            sanitized[str(key)] = sanitize_url(value)
        elif isinstance(value, list):
            sanitized[str(key)] = [item for item in value if isinstance(item, (str, int, float, bool)) or item is None]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[str(key)] = value
    return sanitized


def _current_provider_value(config: dict[str, Any]) -> str | None:
    configured = config.get("configured_provider")
    normalized = _normalize_provider_value(configured) if isinstance(configured, str) else None
    if normalized:
        return normalized
    provider = str(config.get("provider") or "").strip().lower()
    model = str(config.get("model_planner") or "").strip()
    if provider and model:
        return f"{provider}:{model}"
    if provider:
        return provider
    return None


def _normalize_provider_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("env:"):
        raw = raw.removeprefix("env:").strip()
    provider, sep, model = raw.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not provider:
        return None
    if not sep or not model:
        return provider
    return f"{provider}:{model}"


def _fallback_provider_config_snapshot(exc: Exception) -> dict[str, Any]:
    dotenv = _load_dotenv_values()
    provider_value = (_env_or_dotenv(dotenv, "LLM_PROVIDER") or "qwen").strip() or "qwen"
    provider_key, _, model_override = provider_value.partition(":")
    provider_key = (provider_key or "qwen").lower()
    if provider_key == "deepseek":
        api_key = _env_or_dotenv(dotenv, "DEEPSEEK_API_KEY")
        base_url = _env_or_dotenv(dotenv, "DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        model = model_override or _env_or_dotenv(dotenv, "DEEPSEEK_MODEL_PLANNER") or "deepseek-chat"
        writer = model_override or _env_or_dotenv(dotenv, "DEEPSEEK_MODEL_WRITER") or model
        provider = "deepseek"
    elif provider_key == "openai":
        api_key = _env_or_dotenv(dotenv, "OPENAI_API_KEY")
        base_url = _env_or_dotenv(dotenv, "OPENAI_BASE_URL") or "https://api.openai.com/v1"
        model = model_override or _env_or_dotenv(dotenv, "OPENAI_MODEL_PLANNER") or "gpt-4o-mini"
        writer = model_override or _env_or_dotenv(dotenv, "OPENAI_MODEL_WRITER") or model
        provider = "openai"
    else:
        api_key = _env_or_dotenv(dotenv, "DASHSCOPE_API_KEY")
        base_url = _env_or_dotenv(dotenv, "QWEN_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model = model_override or _env_or_dotenv(dotenv, "QWEN_MODEL_PLANNER") or "qwen3.5-flash"
        writer = model_override or _env_or_dotenv(dotenv, "QWEN_MODEL_WRITER") or model
        provider = "qwen"
    return {
        "source": "env_fallback",
        "collection_error": f"{exc.__class__.__name__}: {exc}",
        "configured_provider": provider_value,
        "enabled_providers": _split_csv(_env_or_dotenv(dotenv, "LLM_PROVIDERS") or provider_key),
        "configured_models": _split_csv(_env_or_dotenv(dotenv, "LLM_MODELS")),
        "provider": provider,
        "base_url": sanitize_url(base_url),
        "model_planner": model,
        "model_writer": writer,
        "model_vision_planner": _env_or_dotenv(dotenv, "LLM_VISION_MODEL_PLANNER"),
        "api_key_set": bool(api_key),
    }


def _env_or_dotenv(dotenv: dict[str, str], key: str) -> str | None:
    return os.getenv(key) or dotenv.get(key)


def _load_dotenv_values(path: Path | None = None) -> dict[str, str]:
    env_path = path or Path.cwd() / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _provider_config_snapshot_from_settings(settings: Any) -> dict[str, Any]:
    provider_value = (settings.LLM_PROVIDER or "").strip() or "qwen"
    provider_key, _, model_override = provider_value.partition(":")
    provider_key = (provider_key or "qwen").lower()
    if provider_key == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY") or settings.DEEPSEEK_API_KEY
        base_url = settings.DEEPSEEK_BASE_URL
        model = model_override or settings.DEEPSEEK_MODEL_PLANNER
        writer = model_override or settings.DEEPSEEK_MODEL_WRITER
        provider = "deepseek"
    elif provider_key == "openai":
        api_key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
        base_url = settings.OPENAI_BASE_URL
        model = model_override or settings.OPENAI_MODEL_PLANNER
        writer = model_override or settings.OPENAI_MODEL_WRITER
        provider = "openai"
    else:
        api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
        base_url = settings.QWEN_BASE_URL
        model = model_override or settings.QWEN_MODEL_PLANNER
        writer = model_override or settings.QWEN_MODEL_WRITER
        provider = "qwen"
    return {
        "source": "env",
        "configured_provider": provider_value,
        "enabled_providers": _split_csv(settings.LLM_PROVIDERS),
        "configured_models": _split_csv(settings.LLM_MODELS),
        "provider": provider,
        "base_url": sanitize_url(base_url),
        "model_planner": model,
        "model_writer": writer,
        "model_vision_planner": settings.LLM_VISION_MODEL_PLANNER or None,
        "api_key_set": bool(api_key),
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _merge_count_maps(reports: list[dict[str, Any]], *keys: str) -> dict[str, int]:
    merged: dict[str, int] = {}
    for report in reports:
        if not isinstance(report, dict):
            continue
        for key in keys:
            data = report.get(key)
            if not isinstance(data, dict):
                continue
            for raw_name, raw_count in data.items():
                if not raw_name:
                    continue
                try:
                    count = int(raw_count)
                except (TypeError, ValueError):
                    continue
                merged[str(raw_name)] = merged.get(str(raw_name), 0) + count
    return dict(sorted(merged.items(), key=lambda item: item[1], reverse=True))


def _sum_ints(reports: list[dict[str, Any]], *keys: str) -> int:
    total = 0
    for report in reports:
        if not isinstance(report, dict):
            continue
        for key in keys:
            value = report.get(key)
            if isinstance(value, int):
                total += value
    return total


def _first_key(counts: dict[str, int]) -> str | None:
    return next(iter(counts.keys()), None)


def _has_provider_observation(reports: list[dict[str, Any]]) -> bool:
    observation_keys = {
        "provider_issue_counts",
        "live_provider_issue_counts",
        "provider_action_counts",
        "live_provider_action_counts",
        "environment_failure_count",
        "live_environment_failure_count",
    }
    for report in reports:
        if not isinstance(report, dict):
            continue
        for key in observation_keys:
            if key in report and report.get(key) is not None:
                return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Smart Eats LLM provider health without exposing secrets")
    parser.add_argument("--report", action="append", default=[], help="JSON report to inspect; can be repeated")
    parser.add_argument("--out", default=None, help="Optional output JSON path")
    parser.add_argument("--config-only", action="store_true", help="Only print sanitized provider configuration")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.config_only:
        data = sanitize_provider_config(collect_provider_config_snapshot())
    else:
        reports = [load_json(Path(path)) for path in args.report]
        data = summarize_provider_health(reports)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()

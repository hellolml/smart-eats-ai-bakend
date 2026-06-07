from __future__ import annotations

import json

from scripts.agent_provider_health import (
    collect_provider_config_snapshot,
    sanitize_url,
    summarize_provider_health,
    suggest_provider_values,
)


def test_provider_health_summarizes_observed_provider_issues_without_secret_leak():
    secret = "sk-test-secret"
    health = summarize_provider_health(
        [
            {
                "provider_issue_counts": {"subscription_expired": 2},
                "provider_issue_category_counts": {"provider_auth": 2},
                "provider_action_counts": {"switch_model_or_refresh_provider_subscription": 2},
                "environment_failure_count": 2,
                "live_provider_issue_counts": {"subscription_expired": 1},
                "live_provider_action_counts": {"switch_model_or_refresh_provider_subscription": 1},
            }
        ],
        provider_config={
            "provider": "openai",
            "base_url": "https://example.test/v1",
            "model_planner": "glm-5",
            "api_key_set": True,
            "api_key": secret,
        },
    )

    assert health["status"] == "unhealthy"
    assert health["issue"] == "subscription_expired"
    assert health["category"] == "provider_auth"
    assert health["action"] == "switch_model_or_refresh_provider_subscription"
    assert health["issue_counts"] == {"subscription_expired": 3}
    assert health["action_counts"] == {"switch_model_or_refresh_provider_subscription": 3}
    assert health["suggested_provider_values"] == []
    assert secret not in json.dumps(health, ensure_ascii=False)


def test_provider_health_suggests_configured_model_failover_for_subscription_issue():
    suggestions = suggest_provider_values(
        {
            "configured_provider": "openai:glm-5",
            "provider": "openai",
            "enabled_providers": ["openai"],
            "configured_models": [
                "openai:glm-5",
                "openai:kimi-k2.5",
                "openai:deepseek-v3.2",
                "qwen:qwen3.5-flash",
            ],
            "api_key_set": True,
        },
        issue="subscription_expired",
        category="provider_auth",
    )

    assert suggestions == ["openai:kimi-k2.5", "openai:deepseek-v3.2"]


def test_provider_health_prefers_cross_provider_for_account_auth_issue():
    suggestions = suggest_provider_values(
        {
            "configured_provider": "openai:glm-5",
            "provider": "openai",
            "enabled_providers": ["openai", "qwen"],
            "configured_models": ["openai:glm-5", "openai:kimi-k2.5", "qwen:qwen3.5-flash"],
            "api_key_set": True,
        },
        issue="provider_auth_failed",
        category="provider_auth",
    )

    assert suggestions == ["qwen:qwen3.5-flash", "openai:kimi-k2.5"]


def test_provider_health_marks_missing_api_key_as_config_issue():
    health = summarize_provider_health([], provider_config={"provider": "qwen", "api_key_set": False})

    assert health["status"] == "unhealthy"
    assert health["issue"] == "missing_api_key"
    assert health["category"] == "provider_config"
    assert health["action"] == "set_provider_api_key"


def test_provider_health_marks_clean_report_as_healthy_by_observation():
    health = summarize_provider_health(
        [{"environment_failure_count": 0, "provider_issue_counts": {}}],
        provider_config={"provider": "qwen", "api_key_set": True},
    )

    assert health["status"] == "healthy_by_observation"
    assert health["action"] is None


def test_provider_health_keeps_unobserved_provider_unknown():
    health = summarize_provider_health([{"failed_steps": []}], provider_config={"provider": "qwen", "api_key_set": True})

    assert health["status"] == "unknown"
    assert health["reason"] == "no_provider_observation"


def test_provider_health_sanitizes_url_credentials_and_query():
    assert sanitize_url("https://user:secret@example.test:443/v1?api_key=abc") == "https://example.test:443/v1"


def test_provider_health_config_snapshot_falls_back_without_secret_leak(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai:glm-5")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://user:secret@example.test/v2/coding?token=x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")

    config = collect_provider_config_snapshot()
    payload = json.dumps(config, ensure_ascii=False)

    assert config["provider"] == "openai"
    assert config["model_planner"] == "glm-5"
    assert config["api_key_set"] is True
    assert "sk-secret" not in payload

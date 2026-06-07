"""Alerting — 告警通知管道.

支持三种 Webhook 类型：
- generic: 通用 HTTP POST JSON
- feishu: 飞书自定义机器人 Webhook
- slack: Slack Incoming Webhook

在 evaluate_alert_rules() 触发告警时自动发送通知。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import base64
from datetime import datetime, timezone
from typing import Any

import httpx

from app.common.config import settings

logger = logging.getLogger("app.agent.alerting")

# ── Severity emoji / color ──────────────────────────────────────────

_SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning": "🟡",
    "info": "🔵",
}

_SEVERITY_COLOR = {
    "critical": "#FF0000",
    "warning": "#FFA500",
    "info": "#0000FF",
}


# ── Feishu payload builder ──────────────────────────────────────────


def _build_feishu_payload(alert: dict[str, Any]) -> dict[str, Any]:
    """构建飞书消息卡片 payload."""
    severity = alert.get("severity", "info")
    alert_type = alert.get("alert_type", "unknown")
    payload = alert.get("payload", {})

    actual = payload.get("actual", "?")
    threshold = payload.get("threshold", "?")

    text_lines = [
        f"**{_SEVERITY_EMOJI.get(severity, '⚠️')} 评测告警: {alert_type}**",
        f"严重度: {severity}",
        f"状态: {alert.get('status', 'open')}",
    ]
    if actual != "?" or threshold != "?":
        text_lines.append(f"实际值: {actual} / 阈值: {threshold}")
    if payload.get("window_start"):
        text_lines.append(f"窗口起始: {payload['window_start']}")

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{_SEVERITY_EMOJI.get(severity, '⚠️')} Eval Alert: {alert_type}",
                },
                "template": "red" if severity == "critical" else "orange" if severity == "warning" else "blue",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": "\n".join(text_lines),
                }
            ],
        },
    }

    # 签名（如果配置了 secret）
    if settings.ALERT_WEBHOOK_SECRET:
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        string_to_sign = f"{timestamp}\n{settings.ALERT_WEBHOOK_SECRET}"
        sign = base64.b64encode(hmac.new(
            string_to_sign.encode("utf-8"),
            b"",
            digestmod=hashlib.sha256,
        ).digest()).decode("utf-8")
        card["timestamp"] = timestamp
        card["sign"] = sign

    return card


# ── Slack payload builder ───────────────────────────────────────────


def _build_slack_payload(alert: dict[str, Any]) -> dict[str, Any]:
    """构建 Slack 消息 payload."""
    severity = alert.get("severity", "info")
    alert_type = alert.get("alert_type", "unknown")
    payload = alert.get("payload", {})

    actual = payload.get("actual", "?")
    threshold = payload.get("threshold", "?")

    fields = [
        {"title": "Severity", "value": severity, "short": True},
        {"title": "Status", "value": alert.get("status", "open"), "short": True},
    ]
    if actual != "?" or threshold != "?":
        fields.append({"title": "Actual", "value": str(actual), "short": True})
        fields.append({"title": "Threshold", "value": str(threshold), "short": True})

    return {
        "attachments": [
            {
                "color": _SEVERITY_COLOR.get(severity, "#808080"),
                "title": f"{_SEVERITY_EMOJI.get(severity, '⚠️')} Eval Alert: {alert_type}",
                "fields": fields,
                "footer": "Smart-Eats Eval System",
                "ts": int(datetime.now(timezone.utc).timestamp()),
            }
        ],
    }


# ── Generic payload builder ────────────────────────────────────────


def _build_generic_payload(alert: dict[str, Any]) -> dict[str, Any]:
    """构建通用 Webhook payload."""
    return {
        "alert_type": alert.get("alert_type"),
        "severity": alert.get("severity"),
        "status": alert.get("status"),
        "payload": alert.get("payload"),
        "alert_id": alert.get("id"),
        "created_at": alert.get("created_at"),
        "source": "smart-eats-eval",
    }


# ── Main send function ──────────────────────────────────────────────


async def send_alert_notification(alert: dict[str, Any]) -> bool:
    """发送告警通知到配置的 Webhook.

    Returns:
        True if notification was sent successfully, False otherwise.
    """
    webhook_url = settings.ALERT_WEBHOOK_URL
    if not webhook_url:
        logger.debug("No ALERT_WEBHOOK_URL configured, skipping notification for alert %s", alert.get("id"))
        return False

    webhook_type = settings.ALERT_WEBHOOK_TYPE.lower()

    # 构建对应类型的 payload
    if webhook_type == "feishu":
        payload = _build_feishu_payload(alert)
    elif webhook_type == "slack":
        payload = _build_slack_payload(alert)
    else:
        payload = _build_generic_payload(alert)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if webhook_type == "slack":
                # Slack 使用 form-encoded 格式
                resp = await client.post(webhook_url, data={"payload": json.dumps(payload)})
            else:
                resp = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code >= 400:
                logger.warning(
                    "Alert notification failed: status=%d body=%s alert=%s",
                    resp.status_code, resp.text[:200], alert.get("alert_type"),
                )
                return False
            logger.info(
                "Alert notification sent: type=%s severity=%s alert_id=%s",
                alert.get("alert_type"), alert.get("severity"), alert.get("id"),
            )
            return True
    except Exception as exc:
        logger.warning("Alert notification error: %s alert=%s", exc, alert.get("alert_type"))
        return False


async def send_alert_notifications(alerts: list[dict[str, Any]]) -> dict[str, bool]:
    """批量发送告警通知.

    Returns:
        Dict mapping alert_type -> send_success.
    """
    results: dict[str, bool] = {}
    for alert in alerts:
        alert_type = alert.get("alert_type", "unknown")
        results[alert_type] = await send_alert_notification(alert)
    return results

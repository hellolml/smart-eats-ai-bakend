from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.common.config import settings


def _normalize_servers(servers: dict[str, Any] | None) -> dict[str, Any] | None:
    if not servers:
        return None
    normalized: dict[str, Any] = {}
    for name, config in servers.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(config, dict):
            continue
        expanded = {
            key: os.path.expandvars(value) if isinstance(value, str) else value
            for key, value in config.items()
        }
        transport = expanded.get("transport")
        if not transport and "command" not in expanded:
            url = expanded.get("url")
            if isinstance(url, str) and url:
                lowered = url.lower()
                if "/sse" in lowered or "sse?" in lowered:
                    transport = "sse"
                elif lowered.startswith("http"):
                    transport = "http"
        if transport and "transport" not in expanded:
            expanded = {**expanded, "transport": transport}
        normalized[name.strip()] = expanded
    return normalized or None


def load_servers_from_file(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    config_path = Path(path)
    if not config_path.exists():
        return None
    with config_path.open("r", encoding="utf-8") as handle:
        try:
            data = json.load(handle) or {}
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return _normalize_servers(data)


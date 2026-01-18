from __future__ import annotations

from typing import Any

DEFAULT_SERVER_NAME = "amap"


def default_server_name() -> str:
    return DEFAULT_SERVER_NAME


def default_tool_name(tool_key: str) -> str | None:
    return None


def resolve_tool_name(
    servers: dict[str, Any] | None,
    *,
    server: str,
    tool_key: str,
    default_tool: str | None = None,
) -> str | None:
    if not servers:
        return None
    server_config = servers.get(server)
    if not isinstance(server_config, dict):
        return None
    tools = server_config.get("tools")
    if not isinstance(tools, dict):
        return None
    tool = tools.get(tool_key)
    if isinstance(tool, str):
        return tool.strip() or None
    if isinstance(tool, dict):
        value = tool.get("name") or tool.get("tool") or tool.get("id")
        return str(value).strip() if value else None
    return None

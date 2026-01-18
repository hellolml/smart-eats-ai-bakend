from __future__ import annotations

import asyncio
import inspect
import logging

from app.common.config import settings
from app.infra.mcp import client as mcp_client
from app.infra.mcp import config as mcp_config

logger = logging.getLogger("mcp.probe")


def _tool_name(tool: object) -> str:
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(tool, dict):
        value = tool.get("name") or tool.get("id")
        return str(value) if value else "unknown"
    return "unknown"


async def _get_tools(client: object) -> list[object]:
    for method_name in ("get_tools", "aget_tools"):
        method = getattr(client, method_name, None)
        if not method:
            continue
        result = method()
        if inspect.isawaitable(result):
            return await result
        return result
    raise RuntimeError("MCP client does not support tool listing")


async def main() -> None:
    servers = mcp_config.load_servers_from_file(settings.MCP_SERVERS_CONFIG_PATH)
    if not servers:
        logger.error("No MCP servers config loaded from %s", settings.MCP_SERVERS_CONFIG_PATH)
        return
    logger.info("Loaded MCP servers: %s", list(servers.keys()))
    client = await mcp_client.get_client(servers)
    if not client:
        logger.error("Failed to create MCP client")
        return
    tools = await _get_tools(client)
    tool_names = [_tool_name(tool) for tool in tools or []]
    logger.info("MCP tools count=%s", len(tool_names))
    if tool_names:
        logger.info("MCP tools=%s", tool_names)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

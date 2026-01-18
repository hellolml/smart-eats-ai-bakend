from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
except ImportError:  # pragma: no cover
    MultiServerMCPClient = None

_CLIENTS: dict[str, Any] = {}
_CLIENT_LOCK = asyncio.Lock()
logger = logging.getLogger("mcp")


def _servers_key(servers: dict[str, Any]) -> str:
    return json.dumps(servers, sort_keys=True, ensure_ascii=True)


async def get_client(servers: dict[str, Any] | None) -> Any | None:
    if not MultiServerMCPClient:
        logger.warning("MCP client unavailable: langchain_mcp_adapters not installed")
        return None
    if not servers:
        logger.warning("MCP client unavailable: servers config is empty")
        return None
    key = _servers_key(servers)
    if key in _CLIENTS:
        logger.debug("MCP client cache hit for servers=%s", list(servers.keys()))
        return _CLIENTS[key]
    async with _CLIENT_LOCK:
        if key in _CLIENTS:
            logger.debug("MCP client cache hit after lock for servers=%s", list(servers.keys()))
            return _CLIENTS[key]
        logger.info("Creating MCP client for servers=%s", list(servers.keys()))
        client = MultiServerMCPClient(servers)
        enter = getattr(client, "__aenter__", None)
        if enter:
            logger.warning(
                "MCP client context manager is not supported; skipping __aenter__"
            )
        _CLIENTS[key] = client
        logger.info("MCP client ready for servers=%s", list(servers.keys()))
        return client


async def call_tool(
    servers: dict[str, Any] | None,
    server: str,
    tool_name: str,
    args: dict[str, Any],
) -> Any:
    client = await get_client(servers)
    if not client:
        return None
    tools: list[Any] = []
    for method_name in ("get_tools", "aget_tools"):
        method = getattr(client, method_name, None)
        if not method:
            continue
        params = list(inspect.signature(method).parameters)
        if len(params) == 2:
            result = method(server_name=server)
        else:
            result = method()
        if inspect.isawaitable(result):
            tools = await result
        else:
            tools = result
        break
    if not tools:
        raise RuntimeError("MCP client does not support tool listing")
    logger.info("MCP tools=%s", [getattr(item, "name", None) for item in tools])

    tool = next((item for item in tools if getattr(item, "name", None) == tool_name), None)
    if not tool:
        available = [getattr(item, "name", None) for item in tools]
        raise RuntimeError(f"MCP tool not found: {tool_name}. Available: {available}")

    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(args)
    if hasattr(tool, "invoke"):
        return tool.invoke(args)
    raise RuntimeError("MCP tool does not support invocation")


def extract_payload(response: Any) -> Any:
    if response is None:
        return None
    if isinstance(response, tuple) and len(response) == 2:
        content, _artifact = response
        return content
    if isinstance(response, (dict, list)):
        return response
    for attr in ("content", "result", "data"):
        value = getattr(response, attr, None)
        if value is not None:
            return value
    if isinstance(response, str):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return response
    return response

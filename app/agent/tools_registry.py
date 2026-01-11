from __future__ import annotations

from dataclasses import dataclass
import importlib
import pkgutil
from typing import Any, Awaitable, Callable
import builtins


@dataclass
class ToolSpec:
    name: str
    description: str
    args_schema: dict
    func: Callable[[dict[str, Any]], Awaitable[Any]]


TOOLS: dict[str, ToolSpec] = {}


def register_tool(
    name: str,
    description: str,
    args_schema: dict,
):
    def decorator(func: Callable[[dict[str, Any]], Awaitable[Any]]):
        TOOLS[name] = ToolSpec(
            name=name,
            description=description,
            args_schema=args_schema,
            func=func,
        )
        return func

    return decorator


def load_tools() -> None:
    package_name = "app.agent.tools"
    package = importlib.import_module(package_name)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package_name}.{module.name}")


def list_tools(allowlist: list[str] | None = None) -> list[dict[str, Any]]:
    if not TOOLS:
        load_tools()
    names = set(allowlist or [])
    return [
        {"name": tool.name, "description": tool.description, "args_schema": tool.args_schema}
        for name, tool in TOOLS.items()
        if not allowlist or name in names
    ]


def list() -> list[dict[str, Any]]:
    return list_tools()


def get_tool(name: str, allowlist: list[str] | None = None) -> ToolSpec | None:
    if not TOOLS:
        load_tools()
    if allowlist and name not in allowlist:
        return None
    return TOOLS.get(name)


def preview_result(result: Any) -> Any:
    if isinstance(result, builtins.list):
        return result[:2]
    if isinstance(result, builtins.dict):
        keys = builtins.list(result.keys())[:5]
        return {key: result[key] for key in keys}
    if isinstance(result, builtins.str) and len(result) > 200:
        return result[:200] + "..."
    return result

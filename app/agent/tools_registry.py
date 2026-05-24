from __future__ import annotations

from dataclasses import dataclass
import importlib
import pkgutil
from typing import Annotated, Any, Awaitable, Callable
import builtins
import logging
import re

from pydantic import Field, create_model

try:
    from langchain_core.tools import StructuredTool
except ImportError:  # pragma: no cover
    StructuredTool = None

try:
    from langgraph.prebuilt.tool_node import InjectedState
except ImportError:  # pragma: no cover
    InjectedState = None


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    func: Callable[[dict[str, Any]], Awaitable[Any]]


TOOLS: dict[str, ToolSpec] = {}
logger = logging.getLogger("agent.tools")


def register_tool(
    name: str,
    description: str,
    input_schema: dict,
    output_schema: dict,
):
    def decorator(func: Callable[[dict[str, Any]], Awaitable[Any]]):
        TOOLS[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            func=func,
        )
        logger.info("tool_registered name=%s", name)
        return func

    return decorator


def load_tools() -> None:
    package_name = "app.agent.tools"
    package = importlib.import_module(package_name)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package_name}.{module.name}")
    logger.info("tools_loaded count=%s", len(TOOLS))


def list_tools(allowlist: list[str] | None = None) -> list[dict[str, Any]]:
    if not TOOLS:
        load_tools()
    names = set(allowlist or [])
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
        }
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


def _normalize_json_type(field_schema: dict[str, Any]) -> str | None:
    raw_type = field_schema.get("type")
    if isinstance(raw_type, str):
        return raw_type
    if isinstance(raw_type, builtins.list):
        non_null_types = [item for item in raw_type if isinstance(item, str) and item != "null"]
        if non_null_types:
            return non_null_types[0]
    return None


def _json_schema_to_python_type(field_schema: dict[str, Any]) -> Any:
    schema_type = _normalize_json_type(field_schema)
    if schema_type == "string":
        return str
    if schema_type == "number":
        return float
    if schema_type == "integer":
        return int
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        item_schema = field_schema.get("items")
        item_type = _json_schema_to_python_type(item_schema) if isinstance(item_schema, dict) else Any
        return builtins.list[item_type]
    if schema_type == "object":
        return builtins.dict[str, Any]
    return Any


def _tool_args_model_name(tool_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)
    parts = [part for part in normalized.split("_") if part]
    base = "".join(part.capitalize() for part in parts) or "Tool"
    return f"{base}Args"


def _build_args_model(
    tool_name: str,
    input_schema: dict[str, Any],
    *,
    inject_runtime_context: bool = False,
):
    model_name = _tool_args_model_name(tool_name)
    fields: dict[str, tuple[Any, Any]] = {}

    if isinstance(input_schema, dict):
        properties = input_schema.get("properties")
        if isinstance(properties, dict):
            required = set(input_schema.get("required") or [])
            for field_name, schema in properties.items():
                field_schema = schema if isinstance(schema, dict) else {}
                python_type = _json_schema_to_python_type(field_schema)
                description = field_schema.get("description")

                if field_name in required:
                    fields[field_name] = (python_type, Field(default=..., description=description))
                else:
                    fields[field_name] = (python_type | None, Field(default=None, description=description))

    if inject_runtime_context and InjectedState is not None:
        fields["runtime_context"] = (
            Annotated[dict[str, Any], InjectedState("runtime_context")],
            Field(default_factory=dict),
        )

    return create_model(model_name, **fields)


def get_langchain_tools(
    allowlist: list[str] | None = None,
    runtime_context_factory: Callable[[], dict[str, Any] | None] | None = None,
    *,
    inject_runtime_context: bool = True,
) -> list[Any]:
    if StructuredTool is None:
        raise RuntimeError("langchain_core.tools.StructuredTool is unavailable")
    if not TOOLS:
        load_tools()

    names = set(allowlist or [])
    tools: list[Any] = []

    for name, spec in TOOLS.items():
        if allowlist and name not in names:
            continue

        args_schema = _build_args_model(
            spec.name,
            spec.input_schema,
            inject_runtime_context=inject_runtime_context,
        )

        async def _runner(_spec: ToolSpec = spec, **kwargs: Any) -> Any:
            payload = dict(kwargs)
            runtime_context = payload.pop("runtime_context", None)
            if isinstance(runtime_context, dict):
                payload.update(runtime_context)
            if runtime_context_factory:
                context_payload = runtime_context_factory() or {}
                if isinstance(context_payload, dict):
                    payload.update(context_payload)
            return await _spec.func(payload)

        tool = StructuredTool.from_function(
            coroutine=_runner,
            name=spec.name,
            description=spec.description,
            args_schema=args_schema,
            infer_schema=False,
        )
        tools.append(tool)

    return tools


def to_langchain_tools(
    allowlist: list[str] | None = None,
    runtime_context_factory: Callable[[], dict[str, Any] | None] | None = None,
    *,
    inject_runtime_context: bool = True,
) -> list[Any]:
    return get_langchain_tools(
        allowlist=allowlist,
        runtime_context_factory=runtime_context_factory,
        inject_runtime_context=inject_runtime_context,
    )


def preview_result(result: Any) -> Any:
    if isinstance(result, builtins.list):
        return result[:2]
    if isinstance(result, builtins.dict):
        keys = builtins.list(result.keys())[:5]
        return {key: result[key] for key in keys}
    if isinstance(result, builtins.str) and len(result) > 200:
        return result[:200] + "..."
    return result

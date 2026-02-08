from __future__ import annotations

import asyncio
import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
import logging

from app.agent.state import ChatState
from app.agent.tools_registry import get_tool

logger = logging.getLogger("agent.tools")


class ToolExecutor:
    def __init__(
        self,
        allowed_tools: list[str] | None,
        redis_client: Any,
        db: Any,
        *,
        max_workers: int = 6,
        args_normalizer: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        serial_execution_decider: Callable[[list[dict[str, Any]]], bool] | None = None,
    ) -> None:
        self._allowed_tools = allowed_tools
        self._redis_client = redis_client
        self._db = db
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._args_normalizer = args_normalizer
        self._serial_execution_decider = serial_execution_decider

    def normalize_args(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._args_normalizer:
            return self._args_normalizer(tool_name, args)
        return args

    async def execute_calls(
        self,
        calls: list[dict[str, Any]],
        state: ChatState,
        *,
        servers_path: str | None,
    ) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        force_serial = False
        if self._serial_execution_decider:
            force_serial = self._serial_execution_decider(calls)
        logger.info(
            "tool_executor_plan session_id=%s tool_plan=%s",
            state.session_id,
            [{"name": call.get("name"), "args": call.get("args", {})} for call in calls],
        )
        if force_serial:
            logger.info(
                "tool_executor_mode session_id=%s mode=serial reason=agent_decider",
                state.session_id,
            )
        seen: set[tuple[str, str]] = set()
        results: list[dict[str, Any]] = []
        tasks: list[asyncio.Future] = []
        for index, call in enumerate(calls):
            tool_name = call.get("name")
            args = call.get("args", {})
            if not isinstance(tool_name, str) or not isinstance(args, dict):
                results.append(
                    {
                        "name": tool_name,
                        "args": args,
                        "latency_ms": 0,
                        "result": {"error": "invalid_tool_call"},
                        "index": index,
                    }
                )
                continue
            args = self.normalize_args(tool_name, args)
            dedupe_key = (tool_name, self._args_signature(args))
            if dedupe_key in seen:
                results.append(
                    {
                        "name": tool_name,
                        "args": args,
                        "latency_ms": 0,
                        "result": {"error": "duplicate_tool_call"},
                        "index": index,
                    }
                )
                continue
            seen.add(dedupe_key)
            tool = get_tool(tool_name, self._allowed_tools)
            if not tool:
                results.append(
                    {
                        "name": tool_name,
                        "args": args,
                        "latency_ms": 0,
                        "result": {"error": "unknown_tool"},
                        "index": index,
                    }
                )
                continue
            tool_args = self._build_tool_args(state, args, servers_path)
            ctx = contextvars.copy_context()
            future = loop.run_in_executor(
                self._executor,
                ctx.run,
                self._build_runner(loop, tool.func, tool_name, args, index, tool_args),
            )
            if force_serial:
                results.append(await future)
            else:
                tasks.append(future)

        if tasks:
            results.extend(await asyncio.gather(*tasks))
        results.sort(key=lambda item: item.get("index", 0))
        return results

    def _build_tool_args(
        self,
        state: ChatState,
        args: dict[str, Any],
        servers_path: str | None,
    ) -> dict[str, Any]:
        tool_args = dict(args)
        tool_args["redis_client"] = self._redis_client
        tool_args["db"] = self._db
        tool_args["user_id"] = state.user_id
        tool_args["context"] = state.context
        tool_args["session_id"] = state.session_id
        tool_args["client_ip"] = state.client_ip
        tool_args["last_user_message"] = state.last_user_message or state.message
        tool_args["servers_path"] = servers_path
        return tool_args

    def _args_signature(self, args: dict[str, Any]) -> str:
        try:
            return json.dumps(args, sort_keys=True, ensure_ascii=True)
        except (TypeError, ValueError):
            return str(args)

    def _build_runner(
        self,
        loop: asyncio.AbstractEventLoop,
        tool_func: Any,
        tool_name: str,
        args: dict[str, Any],
        index: int,
        tool_args: dict[str, Any],
    ):
        # Bind per-call values to avoid closure capture across concurrent tool calls.
        def runner() -> dict[str, Any]:
            start = time.perf_counter()
            try:
                future = asyncio.run_coroutine_threadsafe(tool_func(tool_args), loop)
                result = future.result(timeout=15)
            except Exception as exc:
                result = {"error": str(exc)}
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {
                "name": tool_name,
                "args": args,
                "latency_ms": latency_ms,
                "result": result,
                "index": index,
            }

        return runner

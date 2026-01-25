from __future__ import annotations

import asyncio
import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agent.state import ChatState
from app.agent.tools_registry import get_tool


class ToolExecutor:
    def __init__(
        self,
        allowed_tools: list[str] | None,
        redis_client: Any,
        db: Any,
        *,
        max_workers: int = 6,
    ) -> None:
        self._allowed_tools = allowed_tools
        self._redis_client = redis_client
        self._db = db
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    @staticmethod
    def normalize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "geocode_location" and "query" not in args and "location" in args:
            updated = dict(args)
            updated["query"] = updated.pop("location")
            return updated
        return args

    async def execute_calls(
        self,
        calls: list[dict[str, Any]],
        state: ChatState,
        *,
        servers_path: str | None,
    ) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
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
            if tool_name in {"hand_off", "task_completion", "ask_tool_use"}:
                results.append(
                    {
                        "name": tool_name,
                        "args": args,
                        "latency_ms": 0,
                        "result": {"error": "not_implemented"},
                        "index": index,
                    }
                )
                continue
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

            def runner() -> dict[str, Any]:
                start = time.perf_counter()
                try:
                    future = asyncio.run_coroutine_threadsafe(tool.func(tool_args), loop)
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

            tasks.append(loop.run_in_executor(self._executor, ctx.run, runner))

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

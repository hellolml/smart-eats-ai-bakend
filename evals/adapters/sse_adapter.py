"""SSE Adapter — 将 SSE 事件流解析为 EvalTrace.

复用 replay_eval.py 的 SSE 解析逻辑，扩展为完整的轨迹采集。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx

from evals.adapters.trace import EvalTrace, RecoveryEvent, StepTrace

logger = logging.getLogger("evals.sse_adapter")


class SSEAdapter:
    """SSE 事件流解析适配器.

    通过 HTTP 客户端发送消息到后端 SSE 端点，解析完整事件流为 EvalTrace。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def run_and_trace(
        self,
        message: str,
        session_id: str | None = None,
        scene: str | None = None,
        case_id: str = "",
        trial_number: int = 0,
        initial_context: dict[str, Any] | None = None,
    ) -> EvalTrace:
        """发送消息并收集完整 SSE 事件流，返回 EvalTrace.

        Args:
            message: 用户消息
            session_id: 会话 ID（为空时自动创建）
            scene: 指定场景（可选）
            case_id: 关联的评测用例 ID
            trial_number: 试验编号
            initial_context: 初始上下文（如用户位置）
        """
        trace = EvalTrace(
            run_id=uuid.uuid4().hex[:12],
            case_id=case_id,
            trial_number=trial_number,
        )

        start_time = time.monotonic()
        trace.started_at_monotonic = start_time

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            ) as client:
                # 创建会话（如果未提供）
                if not session_id:
                    session_id = await self._create_session(client, scene=scene)

                # 构建请求体
                payload: dict[str, Any] = {"message": message}
                if initial_context:
                    payload["client_context_overrides"] = initial_context

                # 发送 SSE 请求并解析事件流
                async with client.stream(
                    "POST",
                    f"/api/v1/chat/sessions/{session_id}/stream",
                    json=payload,
                    headers={"accept": "text/event-stream"},
                ) as response:
                    response.raise_for_status()

                    current_event = None
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        if line.startswith("event:"):
                            current_event = line.split(":", 1)[1].strip()
                        elif line.startswith("data:") and current_event:
                            data_str = line.split(":", 1)[1].strip()
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            self._record_event(trace, current_event, data)
                            current_event = None

        except httpx.TimeoutException:
            trace.error = f"Request timed out after {self.timeout}s"
            logger.warning("SSE request timed out: case_id=%s", case_id)
        except httpx.HTTPStatusError as exc:
            trace.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.warning("SSE request failed: case_id=%s error=%s", case_id, trace.error)
        except Exception as exc:
            trace.error = str(exc)
            logger.exception("SSE adapter error: case_id=%s", case_id)

        trace.total_duration_ms = (time.monotonic() - start_time) * 1000

        return trace

    async def _create_session(self, client: httpx.AsyncClient, scene: str | None = None) -> str:
        """创建聊天会话"""
        payload: dict[str, Any] = {}
        if scene:
            payload["scene"] = scene
        r = await client.post("/api/v1/chat/sessions", json=payload)
        r.raise_for_status()
        return r.json()["data"]["session_id"]

    def _record_event(self, trace: EvalTrace, event_type: str, data: dict[str, Any]) -> None:
        """将 SSE 事件转换为 StepTrace 并记录到轨迹中"""
        now = time.time()
        step_number = len(trace.steps)

        if event_type == "thinking":
            # 思考开始/结束，仅记录不做详细追踪
            step = StepTrace(
                step_number=step_number,
                event_type="thinking",
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "context":
            # 上下文事件 — 提取 scene、skill、allowed_tools
            trace.allowed_tools = data.get("allowed_tools")
            trace.context_budget = data.get("context_budget")
            scene = data.get("scene") or data.get("actual_scene")
            if scene:
                trace.actual_scene = str(scene)
                trace.scene = trace.actual_scene
            worker = data.get("worker") or data.get("actual_worker") or data.get("agent_id")
            if worker:
                trace.actual_worker = str(worker)
            skills = data.get("skills") or data.get("active_skills")
            if isinstance(skills, list):
                trace.active_skills = [str(s) for s in skills if s]
                trace.skill = ",".join(trace.active_skills) if trace.active_skills else trace.skill
            elif data.get("skill"):
                trace.skill = str(data.get("skill"))
                trace.active_skills = [trace.skill]
            step = StepTrace(
                step_number=step_number,
                event_type="context",
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "tool_call":
            # 工具调用事件
            tool_name = data.get("name", "")
            tool_input = data.get("args")
            result_preview = data.get("result_preview")
            latency_ms = data.get("latency_ms", 0)

            step = StepTrace(
                step_number=step_number,
                event_type="tool_call",
                tool_name=tool_name,
                tool_input=tool_input,
                result_preview=result_preview,
                duration_ms=latency_ms,
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "tool_result":
            # 工具返回事件（需要后端补充此事件类型）
            output = data.get("output")
            if output is None:
                output = data.get("output_preview")
            step = StepTrace(
                step_number=step_number,
                event_type="tool_result",
                tool_name=data.get("name"),
                tool_output=output,
                result_preview=data.get("output_preview") or data.get("result_preview"),
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "recovery":
            # 恢复路径事件（需要后端补充此事件类型）
            recovery = RecoveryEvent(
                path=data.get("path", ""),
                trigger=data.get("trigger", ""),
                message=data.get("message", ""),
            )
            trace.recovery_events.append(recovery)
            step = StepTrace(
                step_number=step_number,
                event_type="recovery",
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "delta":
            # 文本增量输出
            if trace.first_delta_ms is None:
                if trace.started_at_monotonic is not None:
                    trace.first_delta_ms = max(0.0, (time.monotonic() - trace.started_at_monotonic) * 1000)
                else:
                    trace.first_delta_ms = 0.0
            step = StepTrace(
                step_number=step_number,
                event_type="delta",
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "final":
            # 最终结果
            answer = data.get("answer")
            if isinstance(answer, dict):
                trace.final_json = answer

                # 从 final_json 提取 scene（如果有的话）
                final_scene = answer.get("plan_type") or answer.get("scene")
                final_worker = answer.get("agent_id") or answer.get("worker")
                if final_scene and not trace.actual_scene:
                    trace.actual_scene = str(final_scene)
                    trace.scene = trace.actual_scene
                if final_worker and not trace.actual_worker:
                    trace.actual_worker = str(final_worker)

            step = StepTrace(
                step_number=step_number,
                event_type="final",
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "paused":
            step = StepTrace(
                step_number=step_number,
                event_type="paused",
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        elif event_type == "error":
            code = data.get("code")
            message = data.get("message") or data.get("error") or data.get("detail") or "unknown_error"
            trace.error_reason = str(code) if code is not None else "sse_error"
            trace.error = str(message)
            step = StepTrace(
                step_number=step_number,
                event_type="error",
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

        else:
            # 未知事件类型，保留原始数据
            step = StepTrace(
                step_number=step_number,
                event_type=event_type,
                raw_data=data,
                timestamp=now,
            )
            trace.steps.append(step)

    # ── 同步版本（用于简单脚本） ──────────────────────────────

    def run_and_trace_sync(
        self,
        message: str,
        session_id: str | None = None,
        scene: str | None = None,
        case_id: str = "",
        trial_number: int = 0,
        initial_context: dict[str, Any] | None = None,
    ) -> EvalTrace:
        """同步版本：发送消息并收集完整 SSE 事件流"""
        import asyncio
        return asyncio.run(
            self.run_and_trace(
                message=message,
                session_id=session_id,
                scene=scene,
                case_id=case_id,
                trial_number=trial_number,
                initial_context=initial_context,
            )
        )

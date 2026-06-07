"""Agent Runner — Agent 调用封装.

封装 SSEAdapter，提供更高级的 Agent 执行接口。
"""
from __future__ import annotations

import logging
from typing import Any

from evals.adapters.sse_adapter import SSEAdapter
from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase

logger = logging.getLogger("evals.agent_runner")


class AgentRunner:
    """Agent 执行器.

    封装 SSEAdapter，处理 EvalCase 到 EvalTrace 的转换。
    支持场景预设置、上下文注入等。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 60.0):
        self.adapter = SSEAdapter(base_url=base_url, timeout=timeout)

    async def run_case(
        self,
        case: EvalCase,
        trial_number: int = 0,
    ) -> EvalTrace:
        """执行一条评测用例，返回轨迹

        Args:
            case: 评测用例
            trial_number: 试验编号
        """
        # 从 initial_context 构建客户端上下文覆盖
        client_overrides = self._build_client_overrides(case)

        trace = await self.adapter.run_and_trace(
            message=case.task,
            case_id=case.id,
            trial_number=trial_number,
            initial_context=client_overrides,
        )

        trace.expected_scene = case.scene.value
        if not trace.actual_scene:
            trace.error_reason = trace.error_reason or "missing_actual_route"

        return trace

    def _build_client_overrides(self, case: EvalCase) -> dict[str, Any]:
        """从 EvalCase 的 initial_context 构建客户端上下文覆盖"""
        overrides: dict[str, Any] = {}
        ctx = case.initial_context

        # 用户位置
        if ctx.get("user_location"):
            overrides["environment"] = {
                "location": ctx["user_location"]
                if isinstance(ctx["user_location"], dict)
                else {"name": ctx["user_location"]}
            }

        # 冰箱食材
        if "fridge_items" in ctx:
            overrides["fridge_items"] = ctx["fridge_items"]

        return overrides if overrides else {}

"""TrialRunner — 单次试验执行器.

负责执行一条 EvalCase 的单次试验，返回 EvalTrace。
"""
from __future__ import annotations

import logging
from typing import Any

from evals.adapters.agent_runner import AgentRunner
from evals.adapters.fixture_runner import FixtureRunner
from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase

logger = logging.getLogger("evals.trial_runner")


class TrialRunner:
    """单次试验执行器"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 60.0,
        runner_type: str = "live",
        fixture_path: str = "./evals/datasets/fixture_traces.json",
    ):
        self.runner_type = runner_type
        if runner_type == "fixture":
            self.agent_runner = FixtureRunner(fixture_path=fixture_path)
        elif runner_type == "live":
            self.agent_runner = AgentRunner(base_url=base_url, timeout=timeout)
        else:
            raise ValueError(f"Unsupported runner_type: {runner_type}")

    async def run_trial(
        self,
        case: EvalCase,
        trial_number: int = 0,
    ) -> EvalTrace:
        """执行一次试验

        Args:
            case: 评测用例
            trial_number: 试验编号

        Returns:
            EvalTrace: 执行轨迹
        """
        logger.info(
            "Running trial: case=%s trial=%d scene=%s",
            case.id, trial_number, case.scene.value,
        )

        try:
            trace = await self.agent_runner.run_case(case, trial_number=trial_number)
            logger.info(
                "Trial completed: case=%s trial=%d steps=%d has_final=%s",
                case.id, trial_number, len(trace.steps), trace.final_json is not None,
            )
            return trace
        except Exception as exc:
            logger.exception("Trial failed: case=%s trial=%d", case.id, trial_number)
            trace = EvalTrace(
                run_id="error",
                case_id=case.id,
                trial_number=trial_number,
                error=str(exc),
            )
            return trace

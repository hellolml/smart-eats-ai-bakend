"""TravelStateEvaluator — 旅行规划状态机评测器.

评估旅行规划 7 阶段状态机流转的正确性。

阶段序列：
created → ingesting_content → places_extracted →
candidates_ready → candidates_confirmed → itinerary_generated → map_generated
"""
from __future__ import annotations

from evals.adapters.trace import EvalTrace
from evals.datasets.eval_case import EvalCase, Scene
from evals.evaluators.base import BaseEvaluator


class TravelStateEvaluator(BaseEvaluator):
    """旅行规划状态机评测器"""

    # 合法状态转换
    VALID_TRANSITIONS: dict[str, list[str]] = {
        "created": ["ingesting_content"],
        "ingesting_content": ["places_extracted"],
        "places_extracted": ["candidates_ready"],
        "candidates_ready": ["candidates_confirmed", "candidates_ready"],
        "candidates_confirmed": ["itinerary_generated"],
        "itinerary_generated": ["map_generated"],
        "map_generated": [],
    }

    # 终态集合
    TERMINAL_STATES = {"map_generated", "itinerary_generated"}

    @property
    def name(self) -> str:
        return "travel_state"

    def evaluate(self, case: EvalCase, trace: EvalTrace) -> dict[str, float]:
        # 非 travel 场景无需评测状态机
        if case.scene != Scene.TRAVEL:
            return {"state_machine_score": 1.0}

        # 从 trace 中提取状态序列
        states = self._extract_state_sequence(trace)

        if not states:
            # 如果没有显式状态，从 final_json 的 state 字段推断
            if trace.state_value:
                states = [trace.state_value]
            else:
                return {"state_machine_score": 0.0}

        # 1. 状态转换合法性
        transition_score = self._check_transitions(states)

        # 2. 是否到达终态
        reached_terminal = 1.0 if states[-1] in self.TERMINAL_STATES else 0.0

        # 3. 状态完整性（是否跳过了必要阶段）
        completeness = self._check_completeness(states)

        # 综合
        score = transition_score * 0.4 + reached_terminal * 0.3 + completeness * 0.3

        return {
            "state_machine_score": score,
            "transition_validity": transition_score,
            "reached_terminal": reached_terminal,
            "completeness": completeness,
        }

    def _extract_state_sequence(self, trace: EvalTrace) -> list[str]:
        """从 trace 的事件流中提取旅行状态序列"""
        states = []
        seen = set()

        # 从 final_json 的旅行相关字段推断状态
        final = trace.final_json or {}

        if isinstance(final, dict):
            if final.get("places"):
                state = "places_extracted"
                if state not in seen:
                    states.append(state)
                    seen.add(state)

            if final.get("candidates"):
                state = "candidates_ready"
                if state not in seen:
                    states.append(state)
                    seen.add(state)

            if final.get("itinerary"):
                state = "itinerary_generated"
                if state not in seen:
                    states.append(state)
                    seen.add(state)

            if final.get("map"):
                state = "map_generated"
                if state not in seen:
                    states.append(state)
                    seen.add(state)

        # 从 trace.steps 中的 recovery/context 事件提取
        for step in trace.steps:
            if step.raw_data and isinstance(step.raw_data, dict):
                data = step.raw_data.get("data", {})
                if isinstance(data, dict) and "travel_state" in data:
                    state = data["travel_state"]
                    if state not in seen:
                        states.append(state)
                        seen.add(state)

        return states

    def _check_transitions(self, states: list[str]) -> float:
        """检查状态转换是否合法"""
        if len(states) <= 1:
            return 1.0

        valid = 0
        for i in range(1, len(states)):
            prev_state = states[i - 1]
            curr_state = states[i]
            allowed = self.VALID_TRANSITIONS.get(prev_state, [])
            if curr_state in allowed:
                valid += 1
            # 自环不算错误
            elif curr_state == prev_state:
                valid += 1

        return self._safe_divide(valid, len(states) - 1, 1.0)

    def _check_completeness(self, states: list[str]) -> float:
        """检查是否经历了必要阶段"""
        # 最简路径至少需要到达 candidates_ready 或 itinerary_generated
        essential_states = {"candidates_ready", "itinerary_generated"}
        covered = sum(1 for s in essential_states if s in states)
        return self._safe_divide(covered, len(essential_states), 1.0)

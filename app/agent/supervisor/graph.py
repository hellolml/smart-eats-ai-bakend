from __future__ import annotations

from typing import Any

from langgraph_supervisor import create_supervisor

from app.agent.runtime.graph import AgentRuntimeGraphState
from app.agent.supervisor.model import PlannerChatModel
from app.agent.supervisor.workers import WORKER_SPECS, build_worker_agents


SUPERVISOR_PROMPT = """
你是 Smart Eats 的全局 Chat Supervisor，负责把用户请求委派给最合适的专家 agent。

可用专家：
- travel_planner：旅行规划、攻略截图/原文/URL、POI 候选、行程、高德地图。旅行中的餐饮安排仍交给 travel_planner。
- food_advisor：外出吃饭、附近餐厅、今天吃什么、餐厅推荐。
- route_planner：路线、导航、怎么去、带我去某个目的地。
- home_chef：在家做饭、冰箱食材、菜谱。
- general_chat：普通聊天、记忆、无需业务工具的回答。

规则：
- 每次优先委派给一个最合适的专家；只有用户请求明确跨域时才多次委派。
- 不要绕过旅行规划的候选确认、行程确认和地图确认流程。
- 如果专家回复已经足够，直接给用户最终答复，不要改写事实或编造工具结果。
- 输出必须简洁，并保持中文。
""".strip()


def build_supervisor_runtime_graph(
    db: Any,
    redis_client: Any,
    provider: str | None = None,
    resolved_model_config: dict[str, Any] | None = None,
    model: Any | None = None,
) -> Any:
    supervisor_model = model or PlannerChatModel(
        provider=provider,
        resolved_model_config=resolved_model_config,
    )
    agents = build_worker_agents(
        db=db,
        redis_client=redis_client,
        provider=provider,
        resolved_model_config=resolved_model_config,
    )
    return create_supervisor(
        agents,
        model=supervisor_model,
        prompt=SUPERVISOR_PROMPT,
        state_schema=AgentRuntimeGraphState,
        output_mode="last_message",
        add_handoff_messages=False,
        add_handoff_back_messages=False,
        supervisor_name="global_supervisor",
    )


def worker_names() -> list[str]:
    return [spec.name for spec in WORKER_SPECS]

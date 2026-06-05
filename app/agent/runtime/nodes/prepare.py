from __future__ import annotations

from typing import Any


def make_prepare_node(*, db: Any, redis_client: Any, agent_config: Any):
    async def prepare_node(state: dict[str, Any], store: Any = None, config: Any = None) -> dict[str, Any]:
        from app.agent.runtime import builder as runtime_builder

        runtime_db = runtime_builder._runtime_dependency(config, runtime_builder._RUNTIME_CONFIG_DB_KEY, db)
        runtime_redis_client = runtime_builder._runtime_dependency(
            config,
            runtime_builder._RUNTIME_CONFIG_REDIS_KEY,
            redis_client,
        )
        initialized = runtime_builder._initialize_graph_state(state)
        chat_state = runtime_builder._state_from_dict(initialized)
        first_round = (
            chat_state.steps_left <= 0
            and not chat_state.tool_calls
            and not chat_state.observations
        )
        if first_round:
            chat_state.steps_left = agent_config.max_steps
        await runtime_builder._ensure_chat_session(runtime_db, chat_state)
        existing_messages = list(state.get("messages") or [])
        pending_messages = list(initialized.get("messages") or [])
        await runtime_builder._prepare_langgraph_context(
            runtime_db,
            runtime_redis_client,
            chat_state,
            agent_config,
            store=store,
            messages=[*existing_messages, *pending_messages],
            emit_context_event=first_round,
        )

        output = runtime_builder._state_update(chat_state)
        output["messages"] = pending_messages
        return output

    return prepare_node

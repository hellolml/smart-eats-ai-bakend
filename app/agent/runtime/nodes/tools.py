from typing import Any

from langchain_core.runnables import RunnableConfig


def make_tools_node(*, db: Any, redis_client: Any, tool_node: Any, agent_config: Any):
    async def tools_node(state: dict[str, Any], store: Any = None, config: RunnableConfig | None = None) -> dict[str, Any]:
        from app.agent.runtime import builder as runtime_builder

        runtime_db = runtime_builder._runtime_dependency(config, runtime_builder._RUNTIME_CONFIG_DB_KEY, db)
        runtime_redis_client = runtime_builder._runtime_dependency(
            config,
            runtime_builder._RUNTIME_CONFIG_REDIS_KEY,
            redis_client,
        )
        chat_state = runtime_builder._state_from_dict(state)
        ai_messages = runtime_builder._latest_ai_messages(state.get("messages"))
        if not ai_messages:
            return runtime_builder._state_update(chat_state)

        tool_output = await runtime_builder._invoke_tool_node_with_runtime(
            tool_node,
            ai_messages,
            chat_state=chat_state,
            db=runtime_db,
            redis_client=runtime_redis_client,
            store=store,
        )
        tool_messages = runtime_builder._normalize_official_tool_messages(tool_output)
        preview_tool_messages = runtime_builder._preview_tool_messages(tool_messages, chat_state)
        call_args_map = runtime_builder._collect_tool_call_args(ai_messages)

        await runtime_builder._apply_official_tool_postprocess(
            chat_state,
            tool_messages=tool_messages,
            call_args_map=call_args_map,
            db=runtime_db,
            redis_client=runtime_redis_client,
            agent_config=agent_config,
            store=store,
        )
        runtime_builder._finalize_official_after_tools(chat_state, agent_config)

        output = runtime_builder._state_update(chat_state)
        output["messages"] = preview_tool_messages
        return output

    return tools_node

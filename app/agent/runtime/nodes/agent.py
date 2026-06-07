from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.common.config import settings


def _emit_model_usage(chat_state: Any, ai_message: Any, planner: Any) -> None:
    """将 LLM 调用的 token usage 作为 model_usage 事件 emit 到 events 列表."""
    usage = None
    additional_kwargs = getattr(ai_message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        usage = additional_kwargs.get("usage")
    if not isinstance(usage, dict) or not usage:
        return
    provider = usage.get("provider") or getattr(getattr(planner, "config", None), "name", None)
    model_name = usage.get("model_name") or getattr(getattr(planner, "config", None), "model_planner", None)
    chat_state.events.append({
        "event": "model_usage",
        "data": {
            "provider": provider,
            "model": model_name,
            "usage": usage,
        },
    })


def make_agent_node(*, agent_config: Any, planner: Any, registered_tools: list[str]):
    async def agent_node(state: dict[str, Any]) -> dict[str, Any]:
        from app.agent.llm_adapters import OpenAIPlanner
        from app.agent.runtime import builder as runtime_builder
        from app.agent.tools import select_tools

        chat_state = runtime_builder._state_from_dict(state)
        hook_manager = runtime_builder._hook_manager_from_context(chat_state.context)

        short_circuit_final = hook_manager.short_circuit_final(chat_state)
        if short_circuit_final:
            chat_state.final_json = short_circuit_final
            return runtime_builder._state_update(chat_state)

        system = chat_state.context.get("system_prompt") if isinstance(chat_state.context, dict) else None
        if not system:
            system = agent_config.system_prompt_builder({"context": chat_state.context})
        user = chat_state.message or ""
        current_allowed_tools = list(agent_config.core_tool_names)
        if isinstance(chat_state.context, dict) and isinstance(chat_state.context.get("allowed_tools"), list):
            current_allowed_tools = [
                item
                for item in chat_state.context.get("allowed_tools", [])
                if isinstance(item, str) and item in registered_tools
            ]
        active_skills_payload = chat_state.context.get("active_skills") if isinstance(chat_state.context, dict) else None
        active_skill_ids = [
            item.get("id")
            for item in active_skills_payload
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ] if isinstance(active_skills_payload, list) else None
        runtime_builder.logger.info(
            "agent_tools_resolved session_id=%s scene=%s active_skills=%s allowed_tools=%s",
            chat_state.session_id,
            chat_state.scene,
            active_skill_ids,
            current_allowed_tools,
        )
        current_langchain_tools = [*select_tools(current_allowed_tools)]
        if hook_manager.allow_submit_final_answer(chat_state):
            current_langchain_tools.append(runtime_builder._build_submit_final_answer_tool())

        image_parts: list[dict[str, Any]] = []
        active_planner = planner
        has_attachments = bool(isinstance(chat_state.context, dict) and chat_state.context.get("attachments"))
        should_build_vision = settings.LLM_VISION_ENABLED and hook_manager.should_build_vision_input(chat_state)
        runtime_builder.logger.info(
            "vision_check session_id=%s vision_enabled=%s has_attachments=%s should_build=%s",
            chat_state.session_id,
            settings.LLM_VISION_ENABLED,
            has_attachments,
            should_build_vision,
        )
        if should_build_vision:
            try:
                from app.agent.vision import build_vision_content_parts
                from app.infra.minio import get_minio

                image_parts = await build_vision_content_parts(
                    chat_state.context.get("attachments"),
                    minio=await get_minio(),
                )
                runtime_builder.logger.info(
                    "vision_parts_built session_id=%s image_count=%s",
                    chat_state.session_id,
                    len(image_parts),
                )
                if image_parts and settings.LLM_VISION_PROVIDER:
                    active_planner = OpenAIPlanner(provider=settings.LLM_VISION_PROVIDER)
                runtime_builder.logger.info(
                    "vision_llm_call session_id=%s model=%s has_image_parts=%s vision_provider=%s",
                    chat_state.session_id,
                    active_planner.config.model_planner,
                    bool(image_parts),
                    settings.LLM_VISION_PROVIDER,
                )
            except Exception as exc:
                runtime_builder.logger.warning(
                    "vision_input_build_failed session_id=%s reason=%s",
                    chat_state.session_id,
                    str(exc),
                )
                chat_state.events.append(
                    {
                        "event": "vision_error",
                        "data": {
                            "message": f"图片处理失败：{exc}，请重新上传或用文字描述地点"
                        },
                    }
                )

        state_messages = state.get("messages")
        if isinstance(state_messages, list) and state_messages:
            planner_messages = runtime_builder.build_model_messages(
                system_prompt=system,
                summary=chat_state.summary,
                messages=state_messages,
                memories=chat_state.retrieved_memories,
            )
        else:
            planner_messages = [SystemMessage(content=system), HumanMessage(content=user)]
        try:
            ai_message = await active_planner.ainvoke_with_tools(
                planner_messages,
                current_langchain_tools,
                image_parts=image_parts or None,
            )
            _emit_model_usage(chat_state, ai_message, active_planner)
        except Exception as exc:
            msg = str(exc)
            if image_parts and ("image input" in msg.lower() or "image" in msg.lower() or "vision" in msg.lower()):
                runtime_builder.logger.warning(
                    "vision_llm_rejected session_id=%s model=%s reason=%s",
                    chat_state.session_id,
                    active_planner.config.model_planner,
                    msg[:200],
                )
                chat_state.events.append(
                    {
                        "event": "vision_error",
                        "data": {
                            "message": "当前模型不支持图片识别，请用文字描述地点"
                        },
                    }
                )
                image_parts = []
                ai_message = await active_planner.ainvoke_with_tools(
                    planner_messages,
                    current_langchain_tools,
                    image_parts=None,
                )
                _emit_model_usage(chat_state, ai_message, active_planner)
            else:
                raise
        raw_content = ai_message.content
        content = raw_content if isinstance(raw_content, str) else ""
        if content:
            skill_state = dict(chat_state.skill_state or {})
            skill_state["last_ai_message_content"] = content
            ai_message_contents = skill_state.get("ai_message_contents")
            if not isinstance(ai_message_contents, list):
                ai_message_contents = []
            ai_message_contents.append(content)
            skill_state["ai_message_contents"] = ai_message_contents[-6:]
            chat_state.skill_state = skill_state
        forced_tool_calls = hook_manager.forced_tool_calls(chat_state)
        if forced_tool_calls:
            runtime_builder.logger.info(
                "agent_forcing_skill_tool_calls session_id=%s tools=%s",
                chat_state.session_id,
                [call.get("name") for call in forced_tool_calls],
            )
            normalized_tool_calls = forced_tool_calls
        else:
            normalized_tool_calls = ai_message.tool_calls
        runtime_builder.logger.info(
            "agent_tool_choice session_id=%s scene=%s tool_calls=%s",
            chat_state.session_id,
            chat_state.scene,
            [call.get("name") for call in normalized_tool_calls] if isinstance(normalized_tool_calls, list) else [],
        )

        if isinstance(normalized_tool_calls, list) and normalized_tool_calls:
            tool_calls: list[dict[str, Any]] = []
            for index, call in enumerate(normalized_tool_calls):
                tool_name = call.get("name") if isinstance(call, dict) else None
                args = call.get("args") if isinstance(call, dict) else None
                call_id = call.get("id") if isinstance(call, dict) else None
                if not isinstance(tool_name, str) or not isinstance(args, dict):
                    continue
                normalized_args = args
                if tool_name not in current_allowed_tools and tool_name != runtime_builder.SUBMIT_FINAL_TOOL_NAME:
                    continue
                if tool_name in current_allowed_tools:
                    normalized_args = hook_manager.normalize_tool_args(
                        chat_state,
                        tool_name,
                        args,
                    )
                if not isinstance(call_id, str) or not call_id:
                    call_id = f"call_{uuid4().hex[:12]}_{index}"
                tool_calls.append(
                    {
                        "name": tool_name,
                        "args": normalized_args,
                        "id": call_id,
                        "type": "tool_call",
                    }
                )

            if tool_calls:
                max_tool_calls = runtime_builder._skill_max_tool_calls_per_turn(chat_state.context)
                limited_tool_calls = runtime_builder._limit_skill_tool_calls(tool_calls, max_tool_calls=max_tool_calls)
                limited_tool_calls = runtime_builder._enforce_tool_execution_policy(
                    chat_state,
                    limited_tool_calls,
                    agent_config=agent_config,
                )
                if len(limited_tool_calls) < len(tool_calls):
                    runtime_builder.logger.info(
                        "skill_tool_calls_limited session_id=%s max=%s original=%s kept=%s",
                        chat_state.session_id,
                        max_tool_calls,
                        len(tool_calls),
                        len(limited_tool_calls),
                    )
                if limited_tool_calls:
                    ai_message.tool_calls = limited_tool_calls
                    output = runtime_builder._state_update(chat_state)
                    output["messages"] = [ai_message]
                    return output
                if chat_state.final_json:
                    return runtime_builder._state_update(chat_state)

        chat_state.final_json = runtime_builder.final_json_from_text(content)
        return runtime_builder._state_update(chat_state)

    return agent_node

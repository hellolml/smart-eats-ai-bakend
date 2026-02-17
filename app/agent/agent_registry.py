from __future__ import annotations

from dataclasses import dataclass
import importlib
import pkgutil
from typing import Any, Awaitable, Callable

from app.agent.agents.base import (
    default_system_prompt,
    default_writer_prompt,
    normalize_action_from_raw,
)


ToolResultHandler = Callable[[Any, str, Any], dict[str, Any] | None]
IntentResolver = Callable[[Any], str | None]
ToolPlanRouter = Callable[[Any], list[dict[str, Any]] | None]
ContextExtender = Callable[[Any], dict[str, Any]]
ToolArgsNormalizer = Callable[[str, dict[str, Any]], dict[str, Any]]
SerialExecutionDecider = Callable[[list[dict[str, Any]]], bool]
ToolResultPreviewer = Callable[[str, Any], Any]
FinalActionHook = Callable[[Any, dict[str, Any], Any], Awaitable[None]]


@dataclass(frozen=True)
class AgentConfig:
    name: str
    scene: str
    tool_names: list[str]
    max_steps: int = 4
    system_prompt_builder: Callable[[dict[str, Any]], str] = default_system_prompt
    writer_prompt_builder: Callable[[dict[str, Any]], str] = default_writer_prompt
    tool_result_handler: ToolResultHandler | None = None
    action_normalizer: Callable[[str], Any] | None = normalize_action_from_raw
    intent_resolver: IntentResolver | None = None
    tool_plan_router: ToolPlanRouter | None = None
    context_extender: ContextExtender | None = None
    tool_args_normalizer: ToolArgsNormalizer | None = None
    serial_execution_decider: SerialExecutionDecider | None = None
    tool_result_previewer: ToolResultPreviewer | None = None
    final_action_hook: FinalActionHook | None = None


AGENTS: dict[str, AgentConfig] = {}

def create_agent_config(
    name: str,
    scene: str,
    tool_names: list[str],
    max_steps: int = 4,
    system_prompt_builder: Callable[[dict[str, Any]], str] = default_system_prompt,
    writer_prompt_builder: Callable[[dict[str, Any]], str] = default_writer_prompt,
    tool_result_handler: ToolResultHandler | None = None,
    action_normalizer: Callable[[str], Any] | None = normalize_action_from_raw,
    intent_resolver: IntentResolver | None = None,
    tool_plan_router: ToolPlanRouter | None = None,
    context_extender: ContextExtender | None = None,
    tool_args_normalizer: ToolArgsNormalizer | None = None,
    serial_execution_decider: SerialExecutionDecider | None = None,
    tool_result_previewer: ToolResultPreviewer | None = None,
    final_action_hook: FinalActionHook | None = None,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        scene=scene,
        tool_names=tool_names,
        max_steps=max_steps,
        system_prompt_builder=system_prompt_builder,
        writer_prompt_builder=writer_prompt_builder,
        tool_result_handler=tool_result_handler,
        action_normalizer=action_normalizer,
        intent_resolver=intent_resolver,
        tool_plan_router=tool_plan_router,
        context_extender=context_extender,
        tool_args_normalizer=tool_args_normalizer,
        serial_execution_decider=serial_execution_decider,
        tool_result_previewer=tool_result_previewer,
        final_action_hook=final_action_hook,
    )


def register_agent(func=None, **kwargs):
    def decorator(fn):
        if kwargs:
            config = create_agent_config(**kwargs)
        else:
            config = fn()
            if not isinstance(config, AgentConfig):
                raise TypeError("register_agent expects a function returning AgentConfig")
        AGENTS[config.name] = config
        return fn

    if func is None:
        return decorator
    return decorator(func)

def get_agent_config(agent_type: str | None) -> AgentConfig:
    if not AGENTS:
        load_agents()
    if agent_type and agent_type in AGENTS:
        return AGENTS[agent_type]
    return AGENTS["smart_eats"]

def load_agents() -> None:
    package_name = "app.agent.agents"
    package = importlib.import_module(package_name)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package_name}.{module.name}")

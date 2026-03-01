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
BestEffortFallbackHandler = Callable[[Any], dict[str, Any] | None]
FastPathDecider = Callable[[Any], bool]
FastPathSystemPromptBuilder = Callable[[Any], str | None]
FastPathWriterPromptBuilder = Callable[[Any], str]


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
    best_effort_fallback_handler: BestEffortFallbackHandler | None = None
    fast_path_decider: FastPathDecider | None = None
    fast_path_system_prompt_builder: FastPathSystemPromptBuilder | None = None
    fast_path_writer_prompt_builder: FastPathWriterPromptBuilder | None = None


AGENTS: dict[str, AgentConfig] = {}


def _ensure_legacy_smart_eats_bridge() -> None:
    """Legacy-only bridge: keep compatibility for callers still asking registry for smart_eats."""
    if "smart_eats" in AGENTS:
        return
    from app.agent.agents.smart_eats import get_smart_eats_agent_config

    config = get_smart_eats_agent_config()
    AGENTS[config.name] = create_agent_config(
        name=config.name,
        scene=config.scene,
        tool_names=config.tool_names,
        max_steps=config.max_steps,
        system_prompt_builder=config.system_prompt_builder,
        writer_prompt_builder=config.writer_prompt_builder,
        action_normalizer=config.action_normalizer,
        tool_args_normalizer=config.tool_args_normalizer,
        serial_execution_decider=config.serial_execution_decider,
        tool_result_previewer=config.tool_result_previewer,
        final_action_hook=config.final_action_hook,
        best_effort_fallback_handler=config.best_effort_fallback_handler,
        # smart_eats dedicated runtime does not use fast-path; keep bridge minimal.
    )

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
    best_effort_fallback_handler: BestEffortFallbackHandler | None = None,
    fast_path_decider: FastPathDecider | None = None,
    fast_path_system_prompt_builder: FastPathSystemPromptBuilder | None = None,
    fast_path_writer_prompt_builder: FastPathWriterPromptBuilder | None = None,
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
        best_effort_fallback_handler=best_effort_fallback_handler,
        fast_path_decider=fast_path_decider,
        fast_path_system_prompt_builder=fast_path_system_prompt_builder,
        fast_path_writer_prompt_builder=fast_path_writer_prompt_builder,
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
    if agent_type == "smart_eats":
        _ensure_legacy_smart_eats_bridge()
    if not AGENTS:
        load_agents()
    if agent_type and agent_type not in AGENTS:
        load_agents()
    if agent_type == "smart_eats" and "smart_eats" not in AGENTS:
        _ensure_legacy_smart_eats_bridge()
    if agent_type and agent_type in AGENTS:
        return AGENTS[agent_type]
    if AGENTS:
        return next(iter(AGENTS.values()))
    raise LookupError("No legacy agents registered")

def load_agents() -> None:
    package_name = "app.agent.agents"
    package = importlib.import_module(package_name)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package_name}.{module.name}")

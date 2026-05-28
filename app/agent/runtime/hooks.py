from __future__ import annotations

import inspect
import importlib.util
import logging
from pathlib import Path
from typing import Any, Awaitable, Protocol, runtime_checkable

from app.agent.skills.models import SkillSpec

logger = logging.getLogger("agent.runtime.hooks")


@runtime_checkable
class SkillHooks(Protocol):
    def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]:
        return {}

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        return args

    def preview_tool_result(self, state: Any, tool_name: str, result: Any) -> Any | None:
        return None

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        return None

    def should_build_vision_input(self, state: Any) -> bool:
        return False

    def short_circuit_final(self, state: Any) -> dict[str, Any] | None:
        return None

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]] | None:
        return None

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str] | None:
        return None


class BaseSkillHooks:
    def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]:
        return {}

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        return args

    def preview_tool_result(self, state: Any, tool_name: str, result: Any) -> Any | None:
        return None

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        return None

    def should_build_vision_input(self, state: Any) -> bool:
        return False

    def short_circuit_final(self, state: Any) -> dict[str, Any] | None:
        return None

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]] | None:
        return None

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str] | None:
        return None


class SkillHookManager:
    def __init__(self, hooks: list[SkillHooks] | None = None) -> None:
        self.hooks = hooks or []

    @classmethod
    def from_skills(cls, skills: list[SkillSpec]) -> "SkillHookManager":
        hooks: list[SkillHooks] = []
        for skill in skills:
            hook = load_skill_hooks(skill)
            if hook is not None:
                hooks.append(hook)
        return cls(hooks)

    async def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for hook in self.hooks:
            extra = hook.build_context(state, {**context, **merged}, runtime)
            if inspect.isawaitable(extra):
                extra = await extra
            if isinstance(extra, dict) and extra:
                merged = _merge_context(merged, extra)
        return merged

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        normalized = args
        for hook in self.hooks:
            next_args = hook.normalize_tool_args(state, tool_name, normalized)
            if isinstance(next_args, dict):
                normalized = next_args
        return normalized

    def preview_tool_result(self, state: Any, tool_name: str, result: Any) -> Any | None:
        for hook in self.hooks:
            preview = hook.preview_tool_result(state, tool_name, result)
            if preview is not None:
                return preview
        return None

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        for hook in self.hooks:
            final_json = hook.handle_tool_result(state, tool_name, result)
            if isinstance(final_json, dict):
                return final_json
        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        for hook in self.hooks:
            final_json = hook.best_effort_fallback(state)
            if isinstance(final_json, dict):
                return final_json
        return None

    def should_build_vision_input(self, state: Any) -> bool:
        return any(hook.should_build_vision_input(state) for hook in self.hooks)

    def short_circuit_final(self, state: Any) -> dict[str, Any] | None:
        for hook in self.hooks:
            final_json = hook.short_circuit_final(state)
            if isinstance(final_json, dict):
                return final_json
        return None

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for hook in self.hooks:
            hook_calls = hook.forced_tool_calls(state)
            if isinstance(hook_calls, list):
                calls.extend(item for item in hook_calls if isinstance(item, dict))
        return calls

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str]:
        filtered = list(allowed_tools)
        for hook in self.hooks:
            next_tools = hook.filter_allowed_tools(state, filtered)
            if isinstance(next_tools, list):
                filtered = [item for item in next_tools if isinstance(item, str)]
        return filtered


def load_skill_hooks(skill: SkillSpec) -> SkillHooks | None:
    class_path = skill.hooks.class_path if skill.hooks else None
    if not class_path:
        return None
    if not skill.source_path:
        return None
    try:
        module_name, class_name = class_path.rsplit(".", 1)
    except ValueError:
        logger.warning("skill_hook_invalid_class skill=%s class=%s", skill.id, class_path)
        return None

    skill_dir = Path(skill.source_path)
    if module_name == "hooks":
        hook_file = skill_dir / "hooks.py"
    else:
        hook_file = skill_dir / f"{module_name.replace('.', '/')}.py"
    if not hook_file.exists():
        logger.warning("skill_hook_file_missing skill=%s file=%s", skill.id, hook_file)
        return None

    unique_module = f"agent_skill_hooks_{skill.id}_{abs(hash(str(hook_file)))}"
    try:
        spec = importlib.util.spec_from_file_location(unique_module, hook_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        hook_cls = getattr(module, class_name)
        hook = hook_cls()
    except Exception:
        logger.exception("skill_hook_load_failed skill=%s class=%s", skill.id, class_path)
        return None
    return hook if isinstance(hook, SkillHooks) else hook


def _merge_context(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_context(merged[key], value)
        else:
            merged[key] = value
    return merged

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.agents.smart_eats import SmartEatsState, smart_system_prompt


def _write_skill(
    root: Path,
    skill_id: str,
    *,
    priority: int = 50,
    enabled: bool = True,
    scenes: list[str] | None = None,
    keywords: list[str] | None = None,
    tools: list[str] | None = None,
    instructions: str = "Use this skill carefully.",
    safety_lines: list[str] | None = None,
) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "instructions.md").write_text(instructions, encoding="utf-8")
    scene_lines = "\n".join(f"    - {item}" for item in (scenes or ["chat"]))
    keyword_lines = "\n".join(f"    - {item}" for item in (keywords or []))
    tool_lines = "\n".join(f"    - {item}" for item in (tools or []))
    safety_extra = "\n".join(f"  {item}" for item in (safety_lines or []))
    (skill_dir / "skill.yaml").write_text(
        f"""id: {skill_id}
name: {skill_id.replace("_", " ").title()}
version: 1.0.0
description: Test skill {skill_id}
enabled: {str(enabled).lower()}
priority: {priority}
activation:
  scenes:
{scene_lines}
  intents:
    - cook_home
  keywords:
{keyword_lines or "    []"}
  min_score: 1
instructions:
  file: instructions.md
  max_chars: 120
tools:
  allow:
{tool_lines or "    []"}
  require_global_allowlist: true
safety:
  can_override_global_rules: false
  allow_external_tools: false
{safety_extra}
""",
        encoding="utf-8",
    )


def test_skill_loader_reads_enabled_skill_with_instructions(tmp_path):
    from app.agent.skills.loader import load_skills_from_path

    _write_skill(
        tmp_path,
        "home_chef",
        keywords=["冰箱"],
        tools=["get_fridge_items"],
        instructions="Prefer fridge ingredients.",
    )

    skills = load_skills_from_path(tmp_path)

    assert [skill.id for skill in skills] == ["home_chef"]
    assert skills[0].instructions.content == "Prefer fridge ingredients."
    assert skills[0].tools.allow == ["get_fridge_items"]


def test_skill_resolver_activates_by_keyword_and_records_reason(tmp_path):
    from app.agent.skills.loader import load_skills_from_path
    from app.agent.skills.resolver import SkillResolver

    _write_skill(tmp_path, "home_chef", keywords=["冰箱"], tools=["get_fridge_items"])
    skills = load_skills_from_path(tmp_path)
    state = SmartEatsState(session_id="s1", message="冰箱里有鸡蛋，能做什么？", scene="chat")

    active = SkillResolver(skills).resolve(state, {"user_message": state.message})

    assert [skill.id for skill in active.skills] == ["home_chef"]
    assert active.activation_reasons["home_chef"] == ["scene:chat", "keyword:冰箱"]


def test_skill_prompt_composer_orders_by_priority_and_limits_chars(tmp_path):
    from app.agent.skills.loader import load_skills_from_path
    from app.agent.skills.prompt import SkillPromptComposer
    from app.agent.skills.resolver import SkillResolver

    _write_skill(
        tmp_path,
        "low_priority",
        priority=10,
        keywords=["吃"],
        instructions="LOW " * 20,
    )
    _write_skill(
        tmp_path,
        "high_priority",
        priority=90,
        keywords=["吃"],
        instructions="HIGH " * 20,
    )
    state = SmartEatsState(session_id="s1", message="今晚吃什么？", scene="chat")
    active = SkillResolver(load_skills_from_path(tmp_path), max_active=2).resolve(state, {})

    prompt = SkillPromptComposer(max_prompt_chars=170).compose(active)

    assert prompt.index("Skill: high_priority@1.0.0") < prompt.index("Skill: low_priority@1.0.0")
    assert len(prompt) <= 170
    assert "## Active Skills" in prompt


def test_skill_tool_composer_merges_and_denies_unknown_tools(tmp_path):
    from app.agent.skills.loader import load_skills_from_path
    from app.agent.skills.resolver import SkillResolver
    from app.agent.skills.tools import SkillToolComposer

    _write_skill(
        tmp_path,
        "home_chef",
        keywords=["冰箱"],
        tools=["get_fridge_items", "not_registered"],
    )
    state = SmartEatsState(session_id="s1", message="冰箱里有什么？", scene="chat")
    active = SkillResolver(load_skills_from_path(tmp_path)).resolve(state, {})

    output = SkillToolComposer(global_allowlist=["get_user_info", "get_fridge_items"]).compose(
        base_tools=["get_user_info"],
        active_skills=active.skills,
    )

    assert output.allowed_tools == ["get_user_info", "get_fridge_items"]
    assert output.tool_sources["get_fridge_items"] == ["home_chef"]
    assert output.denied_tools["not_registered"] == "not_in_global_allowlist"


def test_skill_runtime_returns_prompt_tools_and_context(tmp_path):
    from app.agent.skills.runtime import SkillRuntime

    _write_skill(
        tmp_path,
        "home_chef",
        keywords=["冰箱"],
        tools=["get_fridge_items"],
        instructions="Prefer fridge ingredients.",
    )
    runtime = SkillRuntime(
        skills_path=tmp_path,
        enabled=True,
        max_active=2,
        max_prompt_chars=1000,
        global_allowlist=["get_user_info", "get_fridge_items"],
    )
    state = SmartEatsState(session_id="s1", message="冰箱里有鸡蛋", scene="chat")

    result = runtime.resolve(state, {"user_message": state.message}, base_tools=["get_user_info"])

    assert result.allowed_tools == ["get_user_info", "get_fridge_items"]
    assert set(result.context) == {"active_skills", "skill_allowed_tools", "skill_diagnostics"}
    assert result.context["active_skills"][0]["id"] == "home_chef"
    assert result.context["skill_diagnostics"]["max_tool_calls_per_turn"] is None
    assert "Prefer fridge ingredients." in result.system_prompt_addendum


def test_skill_runtime_exposes_strictest_tool_call_limit(tmp_path):
    from app.agent.skills.runtime import SkillRuntime

    _write_skill(
        tmp_path,
        "travel_planner",
        scenes=["travel_planner"],
        tools=["travel_search_poi"],
        instructions="Plan trips.",
        safety_lines=["max_tool_calls_per_turn: 4"],
    )
    runtime = SkillRuntime(
        skills_path=tmp_path,
        enabled=True,
        max_active=2,
        max_prompt_chars=1000,
        global_allowlist=["travel_search_poi"],
    )
    state = SmartEatsState(session_id="s1", message="新疆旅行", scene="travel_planner")

    result = runtime.resolve(state, {"user_message": state.message}, base_tools=[])

    assert result.context["skill_diagnostics"]["max_tool_calls_per_turn"] == 4


def test_smart_system_prompt_includes_skill_addendum():
    prompt = smart_system_prompt(
        {
            "context": {"user_message": "冰箱里有什么？"},
            "skill_prompt": "## Active Skills\n\n### Skill: home_chef@1.0.0",
        }
    )

    assert "### Skill: home_chef@1.0.0" in prompt
    assert "Runtime Context" in prompt

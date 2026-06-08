from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.runtime.graph import AgentRuntimeState, skill_system_prompt


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
    scene_lines = "\n".join(f"    - {item}" for item in (scenes or ["chat"]))
    keyword_lines = "\n".join(f"    - {item}" for item in (keywords or []))
    tool_lines = "\n".join(f"    - {item}" for item in (tools or []))
    safety_extra = "\n".join(f"  {item}" for item in (safety_lines or []))
    (skill_dir / "SKILL.md").write_text(
        f"""---
id: {skill_id}
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
---
{instructions}
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
    assert skills[0].instructions.content == ""
    assert skills[0].tools.allow == ["get_fridge_items"]


def test_skill_loader_merges_explicit_reference_includes(tmp_path):
    from app.agent.skills.loader import load_skill_body, load_skills_from_path

    skill_dir = tmp_path / "travel_plan_new"
    skill_dir.mkdir(parents=True)
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "extract-places.md").write_text("Extract image places first.", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        """---
id: travel_plan_new
version: 1.0.0
enabled: true
activation:
  scenes: [travel_planner]
instructions:
  includes:
    - references/extract-places.md
  max_chars: 1000
---
# Travel

Main flow.
""",
        encoding="utf-8",
    )

    skill = load_skills_from_path(tmp_path)[0]
    body = load_skill_body(skill)

    assert "Main flow." in body
    assert "Reference: references/extract-places.md" in body
    assert "Extract image places first." in body


def test_skill_resolver_activates_by_keyword_and_records_reason(tmp_path):
    from app.agent.skills.loader import load_skills_from_path
    from app.agent.skills.resolver import SkillResolver

    _write_skill(tmp_path, "home_chef", keywords=["冰箱"], tools=["get_fridge_items"])
    skills = load_skills_from_path(tmp_path)
    state = AgentRuntimeState(session_id="s1", message="冰箱里有鸡蛋，能做什么？", scene="chat")

    active = SkillResolver(skills).resolve(state, {"user_message": state.message})

    assert [skill.id for skill in active.skills] == ["home_chef"]
    assert active.activation_reasons["home_chef"] == ["scene:chat", "keyword:冰箱"]


def test_skill_resolver_keeps_forced_skill_ahead_of_priority(tmp_path):
    from app.agent.skills.loader import load_skills_from_path
    from app.agent.skills.resolver import SkillResolver

    _write_skill(tmp_path, "ambient_high", priority=99, keywords=["吃"], tools=["memory_search"])
    _write_skill(tmp_path, "forced_low", priority=1, keywords=[], tools=["food_decision"])
    skills = load_skills_from_path(tmp_path)
    state = AgentRuntimeState(session_id="s1", message="今天吃什么？", scene="chat")

    active = SkillResolver(skills, max_active=1).resolve(state, {"forced_skill_ids": ["forced_low"]})

    assert [skill.id for skill in active.skills] == ["forced_low"]
    assert "forced" in active.activation_reasons["forced_low"]


def test_skill_resolver_respects_allowed_skill_ids_as_worker_boundary(tmp_path):
    from app.agent.skills.loader import load_skills_from_path
    from app.agent.skills.resolver import SkillResolver

    _write_skill(tmp_path, "route_planner", keywords=["路线"], tools=["plan_route"])
    _write_skill(tmp_path, "food_assistant", keywords=["餐厅"], tools=["search_restaurants"])
    skills = load_skills_from_path(tmp_path)
    state = AgentRuntimeState(session_id="s1", message="回到刚才那家餐厅，给我路线", scene="route")

    active = SkillResolver(skills).resolve(
        state,
        {"forced_skill_ids": ["route_planner"], "allowed_skill_ids": ["route_planner"]},
    )

    assert [skill.id for skill in active.skills] == ["route_planner"]


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
    state = AgentRuntimeState(session_id="s1", message="今晚吃什么？", scene="chat")
    active = SkillResolver(load_skills_from_path(tmp_path), max_active=2).resolve(state, {})

    prompt = SkillPromptComposer(max_prompt_chars=170).compose(active)

    assert prompt.index("Skill: high_priority@1.0.0") < prompt.index("Skill: low_priority@1.0.0")
    assert len(prompt) <= 170
    assert "## Active Skills" in prompt


def test_skill_prompt_composer_keeps_activation_reasons_out_of_cache_prefix(tmp_path):
    from app.agent.skills.loader import load_skills_from_path
    from app.agent.skills.prompt import SkillPromptComposer
    from app.agent.skills.resolver import SkillResolver

    _write_skill(
        tmp_path,
        "home_chef",
        keywords=["冰箱"],
        tools=["get_fridge_items"],
        instructions="Use fridge ingredients.",
    )
    active = SkillResolver(load_skills_from_path(tmp_path)).resolve(
        AgentRuntimeState(session_id="s1", message="冰箱里有鸡蛋", scene="chat"),
        {},
    )

    prompt = SkillPromptComposer(max_prompt_chars=1000).compose(active)

    assert "Use fridge ingredients." in prompt
    assert "Activation reasons" not in prompt
    assert "keyword:冰箱" not in prompt


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
    state = AgentRuntimeState(session_id="s1", message="冰箱里有什么？", scene="chat")
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
    state = AgentRuntimeState(session_id="s1", message="冰箱里有鸡蛋", scene="chat")

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
    state = AgentRuntimeState(session_id="s1", message="新疆旅行", scene="travel_planner")

    result = runtime.resolve(state, {"user_message": state.message}, base_tools=[])

    assert result.context["skill_diagnostics"]["max_tool_calls_per_turn"] == 4


@pytest.mark.asyncio
async def test_runtime_skill_diagnostics_reflect_filtered_travel_tools():
    from app.agent.runtime import builder as runtime_builder
    from app.agent.runtime.graph import get_agent_runtime_config

    state = AgentRuntimeState(
        session_id="s-travel-filtered-tools",
        message="帮我做杭州1天旅行攻略：西湖",
        scene="travel_planner",
        intent="travel",
    )
    context, _prompt = await runtime_builder._resolve_runtime_skills(
        state,
        {
            "user_message": state.message,
            "ui_scene": "travel_planner",
            "intent": "travel",
            "forced_skill_ids": ["travel_plan_new"],
        },
        get_agent_runtime_config(),
    )

    assert context["allowed_tools"] == ["travel_search_poi"]
    assert context["skill_allowed_tools"] == context["allowed_tools"]
    tool_sources = context["skill_diagnostics"]["tool_sources"]
    assert "memory_search" not in tool_sources
    assert "source_event_search" not in tool_sources
    assert set(tool_sources) == set(context["allowed_tools"])


def test_real_food_worker_skill_set_does_not_activate_route_planner():
    from app.agent.skills.runtime import SkillRuntime

    runtime = SkillRuntime(
        skills_path="agent_skills",
        enabled=True,
        max_active=3,
        max_prompt_chars=1000,
        global_allowlist=[
            "food_decision",
            "get_ip_location",
            "geocode_location",
            "search_restaurants",
            "plan_route",
            "memory_search",
        ],
    )
    state = AgentRuntimeState(session_id="s1", message="附近好吃的", scene="eat")

    result = runtime.resolve(
        state,
        {
            "user_message": state.message,
            "intent": "eat_out",
            "forced_skill_ids": ["food_assistant"],
        },
        base_tools=[],
    )

    active_ids = [item.id for item in result.active_skills]
    assert "food_assistant" in active_ids
    assert "route_planner" not in active_ids
    assert "search_restaurants" in result.allowed_tools
    assert "plan_route" not in result.allowed_tools


def test_skill_system_prompt_includes_skill_addendum():
    prompt = skill_system_prompt(
        {
            "context": {"user_message": "冰箱里有什么？"},
            "skill_prompt": "## Active Skills\n\n### Skill: home_chef@1.0.0",
        }
    )

    assert "### Skill: home_chef@1.0.0" in prompt
    assert "Runtime Context" in prompt
    assert "冰箱里有什么" not in prompt

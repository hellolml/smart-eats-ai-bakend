from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SkillActivationPolicy(BaseModel):
    scenes: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    min_score: int = 1


class SkillInstructions(BaseModel):
    file: str = "instructions.md"
    max_chars: int = 3000
    content: str = ""


class SkillToolPolicy(BaseModel):
    allow: list[str] = Field(default_factory=list)
    require_global_allowlist: bool = True


class SkillContextPolicy(BaseModel):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class SkillSafetyPolicy(BaseModel):
    can_override_global_rules: bool = False
    allow_external_tools: bool = False
    max_tool_calls_per_turn: int | None = None


class SkillSpec(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    enabled: bool = True
    priority: int = 50
    activation: SkillActivationPolicy = Field(default_factory=SkillActivationPolicy)
    instructions: SkillInstructions = Field(default_factory=SkillInstructions)
    tools: SkillToolPolicy = Field(default_factory=SkillToolPolicy)
    context: SkillContextPolicy | None = None
    safety: SkillSafetyPolicy = Field(default_factory=SkillSafetyPolicy)
    source_path: Path | None = None


class SkillPromptBlock(BaseModel):
    skill_id: str
    version: str
    priority: int
    content: str
    reasons: list[str] = Field(default_factory=list)


class ActiveSkillSet(BaseModel):
    skills: list[SkillSpec] = Field(default_factory=list)
    activation_reasons: dict[str, list[str]] = Field(default_factory=dict)
    prompt_blocks: list[SkillPromptBlock] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    context_extensions: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SkillToolComposerOutput(BaseModel):
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: dict[str, str] = Field(default_factory=dict)
    tool_sources: dict[str, list[str]] = Field(default_factory=dict)


class ActiveSkillInfo(BaseModel):
    id: str
    version: str
    reasons: list[str] = Field(default_factory=list)


class SkillDiagnostics(BaseModel):
    prompt_chars: int = 0
    denied_tools: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    tool_sources: dict[str, list[str]] = Field(default_factory=dict)
    max_tool_calls_per_turn: int | None = None


class SkillRuntimeResult(BaseModel):
    active_skills: list[ActiveSkillInfo] = Field(default_factory=list)
    system_prompt_addendum: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    diagnostics: SkillDiagnostics = Field(default_factory=SkillDiagnostics)


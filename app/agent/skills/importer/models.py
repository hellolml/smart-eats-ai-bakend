from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ExternalSkillPackage(BaseModel):
    name: str
    description: str = ""
    version: str = "0.1.0"
    instructions: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class InstallReport(BaseModel):
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    blocked_files: list[str] = Field(default_factory=list)
    risk_level: str = "low"


class InstallResult(BaseModel):
    skill_id: str
    install_path: Path
    report: InstallReport

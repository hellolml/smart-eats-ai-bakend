from __future__ import annotations

import re
from typing import Any

from app.agent.skills.importer.models import ExternalSkillPackage, InstallReport


EXTERNAL_TOOL_MAPPING = {
    "amap": ["travel_search_poi", "plan_route", "geocode_location"],
    "lbs": ["travel_search_poi", "plan_route", "geocode_location"],
    "map": ["travel_search_poi", "plan_route"],
    "poi": ["travel_search_poi"],
    "route": ["plan_route"],
    "travel": ["travel_search_poi", "plan_route", "travel_create_personal_map"],
}


DENIED_EXTERNAL_TOOLS = {
    "shell": "external shell execution is not allowed",
    "bash": "external shell execution is not allowed",
    "python": "external code execution is not allowed",
    "node": "external code execution is not allowed",
    "browser": "external browser automation requires manual approval",
    "file_write": "external file writes require manual approval",
    "filesystem": "external filesystem access requires manual approval",
}


def slugify_skill_id(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return value or "imported_skill"


def build_install_report(package: ExternalSkillPackage, *, blocked_files: list[str] | None = None) -> InstallReport:
    allowed: list[str] = []
    denied: dict[str, str] = {}
    requested_tools = _requested_external_tools(package)

    for tool in requested_tools:
        normalized = tool.lower().replace("-", "_")
        if normalized in DENIED_EXTERNAL_TOOLS:
            denied[tool] = DENIED_EXTERNAL_TOOLS[normalized]
            continue
        for mapped in EXTERNAL_TOOL_MAPPING.get(normalized, []):
            if mapped not in allowed:
                allowed.append(mapped)

    risk_level = "medium" if denied or blocked_files else "low"
    return InstallReport(
        allowed_tools=allowed,
        denied_tools=denied,
        blocked_files=sorted(blocked_files or []),
        risk_level=risk_level,
    )


def package_to_skill_manifest(
    package: ExternalSkillPackage,
    *,
    skill_id: str,
    report: InstallReport,
) -> dict[str, Any]:
    return {
        "id": skill_id,
        "name": package.name,
        "version": package.version,
        "description": package.description,
        "enabled": True,
        "priority": 40,
        "activation": {
            "scenes": [],
            "intents": [],
            "keywords": _keywords_for(package),
            "min_score": 1,
        },
        "instructions": {
            "file": "instructions.md",
            "max_chars": 3000,
        },
        "tools": {
            "allow": report.allowed_tools,
            "require_global_allowlist": True,
        },
        "safety": {
            "can_override_global_rules": False,
            "allow_external_tools": False,
        },
    }


def _requested_external_tools(package: ExternalSkillPackage) -> list[str]:
    candidates: list[Any] = []
    openclaw = package.metadata.get("openclaw")
    clawdbot = package.metadata.get("clawdbot")
    clawdis = package.metadata.get("clawdis")
    for metadata in (openclaw, clawdbot, clawdis):
        if isinstance(metadata, dict):
            candidates.append(metadata.get("tools"))
            candidates.append(metadata.get("requires"))

    requested: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, str):
            requested.append(candidate)
        elif isinstance(candidate, list):
            requested.extend(item for item in candidate if isinstance(item, str))
    return list(dict.fromkeys(requested))


def _keywords_for(package: ExternalSkillPackage) -> list[str]:
    words = [package.name, package.description]
    text = " ".join(item for item in words if item)
    keywords = [part for part in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", text) if len(part) >= 2]
    return list(dict.fromkeys(keywords[:12]))

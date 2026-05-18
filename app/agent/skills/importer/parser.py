from __future__ import annotations

import re
from typing import Any

import yaml

from app.agent.skills.importer.models import ExternalSkillPackage


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def parse_skill_markdown(content: str) -> ExternalSkillPackage:
    raw_meta: dict[str, Any] = {}
    body = content.strip()
    match = _FRONTMATTER_RE.match(content)
    if match:
        loaded = yaml.safe_load(match.group(1)) or {}
        if isinstance(loaded, dict):
            raw_meta = loaded
        body = content[match.end() :].strip()

    name = str(raw_meta.get("name") or _infer_name(body) or "imported-skill")
    description = str(raw_meta.get("description") or "")
    version = str(raw_meta.get("version") or "0.1.0")
    metadata = raw_meta.get("metadata") if isinstance(raw_meta.get("metadata"), dict) else {}

    return ExternalSkillPackage(
        name=name,
        description=description,
        version=version,
        instructions=body,
        metadata=metadata,
    )


def _infer_name(body: str) -> str | None:
    match = _HEADING_RE.search(body)
    if not match:
        return None
    return match.group(1).strip()

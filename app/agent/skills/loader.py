from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.agent.skills.models import SkillInstructions, SkillSpec

logger = logging.getLogger("agent.skills.loader")


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.exception("skill_manifest_load_failed path=%s", path)
        return None
    if not isinstance(data, dict):
        logger.warning("skill_manifest_invalid path=%s reason=not_object", path)
        return None
    return data


def _load_instruction_content(skill_dir: Path, instructions: SkillInstructions) -> str:
    instruction_path = skill_dir / instructions.file
    try:
        content = instruction_path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning(
            "skill_instructions_missing skill_dir=%s file=%s",
            skill_dir,
            instructions.file,
        )
        return ""
    if instructions.max_chars > 0 and len(content) > instructions.max_chars:
        return content[: instructions.max_chars].rstrip()
    return content


def load_skills_from_path(path: str | Path | None) -> list[SkillSpec]:
    if not path:
        return []
    root = Path(path)
    if not root.exists() or not root.is_dir():
        logger.info("skill_registry_path_missing path=%s", root)
        return []

    skills: list[SkillSpec] = []
    for skill_dir in sorted(item for item in root.iterdir() if item.is_dir()):
        manifest_path = skill_dir / "skill.yaml"
        if not manifest_path.exists():
            continue
        raw = _read_yaml(manifest_path)
        if not raw:
            continue
        try:
            spec = SkillSpec.model_validate({**raw, "source_path": skill_dir})
        except Exception:
            logger.exception("skill_manifest_validate_failed path=%s", manifest_path)
            continue
        if not spec.enabled:
            continue
        content = _load_instruction_content(skill_dir, spec.instructions)
        spec = spec.model_copy(
            update={"instructions": spec.instructions.model_copy(update={"content": content})}
        )
        skills.append(spec)

    logger.info("skill_registry_loaded count=%s path=%s", len(skills), root)
    return skills


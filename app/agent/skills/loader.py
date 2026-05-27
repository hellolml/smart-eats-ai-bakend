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


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str] | None:
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        logger.exception("skill_frontmatter_parse_failed")
        return None
    if not isinstance(metadata, dict):
        return None
    return metadata, parts[2].strip()


def _read_skill_frontmatter(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("skill_file_load_failed path=%s", path)
        return None
    split = _split_frontmatter(raw)
    if not split:
        logger.warning("skill_file_invalid_frontmatter path=%s", path)
        return None
    metadata, _body = split
    return metadata


def load_skill_body(skill: SkillSpec) -> str:
    if not skill.source_path:
        return ""
    skill_path = Path(skill.source_path) / skill.skill_file
    try:
        raw = skill_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("skill_body_missing skill=%s path=%s", skill.id, skill_path)
        return ""
    split = _split_frontmatter(raw)
    body = split[1] if split else raw.strip()
    max_chars = skill.instructions.max_chars
    if max_chars > 0 and len(body) > max_chars:
        return body[:max_chars].rstrip()
    return body


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
    skill_files = sorted(root.rglob("SKILL.md"))
    legacy_manifests = sorted(root.rglob("skill.yaml")) if not skill_files else []

    for manifest_path in [*skill_files, *legacy_manifests]:
        if any(part.startswith(".") for part in manifest_path.relative_to(root).parts):
            continue
        skill_dir = manifest_path.parent
        is_skill_md = manifest_path.name == "SKILL.md"
        raw = _read_skill_frontmatter(manifest_path) if is_skill_md else _read_yaml(manifest_path)
        if not raw:
            continue
        if is_skill_md:
            raw = {**raw, "skill_file": manifest_path.name}
        try:
            spec = SkillSpec.model_validate({**raw, "source_path": skill_dir})
        except Exception:
            logger.exception("skill_manifest_validate_failed path=%s", manifest_path)
            continue
        if not spec.enabled:
            continue
        if not is_skill_md:
            content = _load_instruction_content(skill_dir, spec.instructions)
            spec = spec.model_copy(
                update={"instructions": spec.instructions.model_copy(update={"content": content})}
            )
        skills.append(spec)

    logger.info("skill_registry_loaded count=%s path=%s", len(skills), root)
    return skills

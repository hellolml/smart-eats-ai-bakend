from __future__ import annotations

from pathlib import Path


ALLOWED_SUPPORT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}
MAX_SUPPORT_FILE_BYTES = 256 * 1024


class SkillValidationError(ValueError):
    pass


def validate_relative_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SkillValidationError(f"unsafe path: {path}")
    return candidate


def is_supported_reference_file(path: Path) -> bool:
    return path.suffix.lower() in ALLOWED_SUPPORT_SUFFIXES


def should_copy_reference_file(path: Path) -> tuple[bool, str | None]:
    if path.name.lower() == "skill.md" or path.name == "SKILL.md":
        return False, "manifest"
    if not is_supported_reference_file(path):
        return False, "unsupported_file_type"
    try:
        if path.stat().st_size > MAX_SUPPORT_FILE_BYTES:
            return False, "file_too_large"
    except OSError:
        return False, "unreadable_file"
    return True, None

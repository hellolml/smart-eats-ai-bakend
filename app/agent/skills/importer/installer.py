from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml

from app.agent.skills.importer.adapter import (
    build_install_report,
    package_to_skill_manifest,
    slugify_skill_id,
)
from app.agent.skills.importer.models import ExternalSkillPackage, InstallResult
from app.agent.skills.importer.parser import parse_skill_markdown
from app.agent.skills.importer.validator import (
    SkillValidationError,
    should_copy_reference_file,
    validate_relative_path,
)


class ExternalSkillInstaller:
    def __init__(self, install_root: str | Path) -> None:
        self.install_root = Path(install_root)

    def install_from_directory(self, source_dir: str | Path) -> InstallResult:
        source = Path(source_dir)
        manifest_path = self._find_skill_markdown(source)
        package = parse_skill_markdown(manifest_path.read_text(encoding="utf-8"))
        blocked_files = self._blocked_files(source)
        result = self._install_package(
            package,
            blocked_files=blocked_files,
            origin={
                "source_type": "local_directory",
                "source_path": str(source),
                "original_format": "SKILL.md",
            },
        )
        self._copy_reference_files(source, result.install_path / "references")
        return result

    def install_from_skill_markdown(self, content: str, *, origin: dict[str, Any] | None = None) -> InstallResult:
        package = parse_skill_markdown(content)
        return self._install_package(
            package,
            blocked_files=[],
            origin={
                "source_type": "markdown",
                "original_format": "SKILL.md",
                **(origin or {}),
            },
        )

    def install_from_zip_bytes(self, data: bytes, *, source_name: str = "skill.zip") -> InstallResult:
        with tempfile.TemporaryDirectory(prefix="smarteats_skill_") as temp_dir:
            temp_root = Path(temp_dir)
            extract_dir = temp_root / "extracted"
            extract_dir.mkdir()
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for member in archive.infolist():
                    safe_name = validate_relative_path(member.filename)
                    if member.is_dir():
                        continue
                    destination = extract_dir / safe_name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(member))
            source_dir = self._source_root_from_extract(extract_dir)
            return self.install_from_directory(source_dir)

    def _install_package(
        self,
        package: ExternalSkillPackage,
        *,
        blocked_files: list[str],
        origin: dict[str, Any],
    ) -> InstallResult:
        skill_id = slugify_skill_id(package.name)
        report = build_install_report(package, blocked_files=blocked_files)
        target = self.install_root / "imported" / skill_id
        if target.exists():
            raise SkillValidationError(f"skill already installed: {skill_id}")

        target.mkdir(parents=True)
        (target / "references").mkdir()

        manifest = package_to_skill_manifest(package, skill_id=skill_id, report=report)
        (target / "skill.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        (target / "instructions.md").write_text(package.instructions.strip() + "\n", encoding="utf-8")
        (target / "_origin.json").write_text(
            json.dumps(origin, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (target / "_install_report.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return InstallResult(skill_id=skill_id, install_path=target, report=report)

    def _find_skill_markdown(self, source_dir: Path) -> Path:
        for filename in ("SKILL.md", "skill.md"):
            candidate = source_dir / filename
            if candidate.exists() and candidate.is_file():
                return candidate
        raise SkillValidationError(f"SKILL.md not found: {source_dir}")

    def _source_root_from_extract(self, extract_dir: Path) -> Path:
        matches = sorted(extract_dir.rglob("SKILL.md")) + sorted(extract_dir.rglob("skill.md"))
        if not matches:
            raise SkillValidationError("SKILL.md not found in archive")
        if len(matches) > 1:
            raise SkillValidationError("multiple SKILL.md files found in archive")
        return matches[0].parent

    def _blocked_files(self, source_dir: Path) -> list[str]:
        blocked: list[str] = []
        for path in sorted(item for item in source_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(source_dir)
            copy, reason = should_copy_reference_file(path)
            if not copy and reason != "manifest":
                blocked.append(relative.as_posix())
        return blocked

    def _copy_reference_files(self, source_dir: Path, references_dir: Path) -> None:
        for path in sorted(item for item in source_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(source_dir)
            validate_relative_path(relative)
            copy, _reason = should_copy_reference_file(path)
            if not copy:
                continue
            destination = references_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)

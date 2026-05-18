from __future__ import annotations

import ipaddress
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from app.agent.skills.importer.installer import ExternalSkillInstaller
from app.agent.skills.importer.validator import SkillValidationError
from app.agent.skills.loader import load_skills_from_path


class SkillManagementService:
    def __init__(self, skills_root: str | Path, *, url_fetcher: Callable[[str], str] | None = None) -> None:
        self.skills_root = Path(skills_root)
        self.url_fetcher = url_fetcher or self._fetch_url

    def list_skills(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for skill in load_skills_from_path(self.skills_root):
            source = "imported" if self._is_imported_path(skill.source_path) else "built_in"
            items.append(
                {
                    "id": skill.id,
                    "name": skill.name,
                    "version": skill.version,
                    "description": skill.description,
                    "enabled": skill.enabled,
                    "source": source,
                    "tools": skill.tools.allow,
                    "install_report": self._read_install_report(skill.source_path),
                }
            )
        return items

    def import_from_directory(self, source_dir: str | Path) -> dict[str, Any]:
        result = ExternalSkillInstaller(self.skills_root).install_from_directory(source_dir)
        return self._install_result_to_dict(result)

    def import_from_zip_bytes(self, data: bytes, *, source_name: str = "skill.zip") -> dict[str, Any]:
        result = ExternalSkillInstaller(self.skills_root).install_from_zip_bytes(data, source_name=source_name)
        return self._install_result_to_dict(result)

    def import_from_url(self, url: str) -> dict[str, Any]:
        self._validate_url(url)
        content = self.url_fetcher(url)
        result = ExternalSkillInstaller(self.skills_root).install_from_skill_markdown(
            content,
            origin={
                "source_type": "url",
                "source_url": url,
                "original_format": "SKILL.md",
            },
        )
        return self._install_result_to_dict(result)

    def uninstall(self, skill_id: str) -> dict[str, Any]:
        safe_id = self._validate_skill_id(skill_id)
        target = self.skills_root / "imported" / safe_id
        if not target.exists():
            return {"deleted": False, "skill_id": safe_id}
        shutil.rmtree(target)
        return {"deleted": True, "skill_id": safe_id}

    def _validate_skill_id(self, skill_id: str) -> str:
        if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
            raise SkillValidationError(f"unsafe skill id: {skill_id}")
        return skill_id

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SkillValidationError(f"unsupported skill url: {url}")
        host = parsed.hostname or ""
        if host.lower() in {"localhost", "localhost.localdomain"}:
            raise SkillValidationError(f"unsupported skill url host: {host}")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise SkillValidationError(f"unsupported skill url host: {host}")

    def _fetch_url(self, url: str) -> str:
        try:
            with urlopen(url, timeout=10) as response:
                content = response.read(512 * 1024 + 1)
        except (OSError, URLError) as exc:
            raise SkillValidationError(f"skill url fetch failed: {url}") from exc
        if len(content) > 512 * 1024:
            raise SkillValidationError("skill url content too large")
        return content.decode("utf-8")

    def _is_imported_path(self, source_path: Path | None) -> bool:
        if source_path is None:
            return False
        try:
            relative = source_path.resolve().relative_to((self.skills_root / "imported").resolve())
        except ValueError:
            return False
        return bool(relative.parts)

    def _read_install_report(self, source_path: Path | None) -> dict[str, Any] | None:
        if source_path is None:
            return None
        report_path = source_path / "_install_report.json"
        if not report_path.exists():
            return None
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _install_result_to_dict(self, result: Any) -> dict[str, Any]:
        return {
            "skill_id": result.skill_id,
            "install_path": str(result.install_path),
            "report": result.report.model_dump(),
        }

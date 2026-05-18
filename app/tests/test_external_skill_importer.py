from __future__ import annotations

import io
import zipfile
from pathlib import Path


def _write_external_skill(root: Path, *, body: str | None = None) -> Path:
    skill_dir = root / "amap-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        body
        or """---
name: amap-lbs
description: Use AMap LBS APIs for POI and route planning.
version: 1.2.3
metadata:
  openclaw:
    tools:
      - amap
      - route
---

# AMap Skill

Use this skill when users ask about maps, POIs, routes, or travel planning.
""",
        encoding="utf-8",
    )
    (skill_dir / "references.md").write_text("AMap reference notes.", encoding="utf-8")
    return skill_dir


def test_parse_skill_markdown_reads_frontmatter_and_instructions():
    from app.agent.skills.importer.parser import parse_skill_markdown

    package = parse_skill_markdown(
        """---
name: amap-lbs
description: Use AMap LBS APIs.
version: 1.2.3
---

# AMap Skill

Use this for routes.
"""
    )

    assert package.name == "amap-lbs"
    assert package.description == "Use AMap LBS APIs."
    assert package.version == "1.2.3"
    assert "Use this for routes." in package.instructions


def test_install_from_directory_converts_skill_md_to_smarteats_skill(tmp_path):
    from app.agent.skills.importer.installer import ExternalSkillInstaller
    from app.agent.skills.loader import load_skills_from_path

    source_dir = _write_external_skill(tmp_path)
    install_root = tmp_path / "agent_skills"

    result = ExternalSkillInstaller(install_root).install_from_directory(source_dir)

    assert result.skill_id == "amap_lbs"
    assert result.report.allowed_tools == ["travel_search_poi", "plan_route", "geocode_location"]
    assert result.report.denied_tools == {}
    assert (install_root / "imported" / "amap_lbs" / "skill.yaml").exists()
    assert (install_root / "imported" / "amap_lbs" / "instructions.md").exists()
    assert (install_root / "imported" / "amap_lbs" / "references" / "references.md").exists()

    skills = load_skills_from_path(install_root)

    assert [skill.id for skill in skills] == ["amap_lbs"]
    assert skills[0].tools.allow == ["travel_search_poi", "plan_route", "geocode_location"]
    assert "maps, POIs, routes" in skills[0].instructions.content


def test_install_blocks_script_files_without_copying_them(tmp_path):
    from app.agent.skills.importer.installer import ExternalSkillInstaller

    source_dir = _write_external_skill(tmp_path)
    (source_dir / "run.sh").write_text("echo unsafe", encoding="utf-8")
    install_root = tmp_path / "agent_skills"

    result = ExternalSkillInstaller(install_root).install_from_directory(source_dir)

    assert "run.sh" in result.report.blocked_files
    assert not (install_root / "imported" / "amap_lbs" / "references" / "run.sh").exists()


def test_install_from_zip_rejects_path_traversal(tmp_path):
    from app.agent.skills.importer.installer import ExternalSkillInstaller
    from app.agent.skills.importer.validator import SkillValidationError

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("skill/SKILL.md", "# Bad Skill\n")
        archive.writestr("../escape.md", "escape")

    installer = ExternalSkillInstaller(tmp_path / "agent_skills")

    try:
        installer.install_from_zip_bytes(buf.getvalue(), source_name="bad.zip")
    except SkillValidationError as exc:
        assert "unsafe path" in str(exc)
    else:
        raise AssertionError("expected unsafe zip path to be rejected")

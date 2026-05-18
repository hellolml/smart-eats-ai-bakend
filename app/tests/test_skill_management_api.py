from __future__ import annotations

from pathlib import Path


def _write_external_skill(root: Path) -> Path:
    skill_dir = root / "route-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: route-helper
description: Route and map helper.
version: 0.2.0
metadata:
  openclaw:
    tools:
      - route
---

# Route Helper

Use for route planning.
""",
        encoding="utf-8",
    )
    return skill_dir


def test_skill_management_service_lists_and_imports_directory(tmp_path):
    from app.agent.skills.management import SkillManagementService

    source_dir = _write_external_skill(tmp_path)
    service = SkillManagementService(tmp_path / "agent_skills")

    result = service.import_from_directory(source_dir)
    skills = service.list_skills()

    assert result["skill_id"] == "route_helper"
    assert skills == [
        {
            "id": "route_helper",
            "name": "route-helper",
            "version": "0.2.0",
            "description": "Route and map helper.",
            "enabled": True,
            "source": "imported",
            "tools": ["plan_route"],
            "install_report": {
                "allowed_tools": ["plan_route"],
                "denied_tools": {},
                "warnings": [],
                "blocked_files": [],
                "risk_level": "low",
            },
        }
    ]


def test_skill_management_service_uninstalls_only_imported_skills(tmp_path):
    from app.agent.skills.management import SkillManagementService
    from app.agent.skills.importer.validator import SkillValidationError

    source_dir = _write_external_skill(tmp_path)
    service = SkillManagementService(tmp_path / "agent_skills")
    service.import_from_directory(source_dir)

    assert service.uninstall("route_helper") == {"deleted": True, "skill_id": "route_helper"}
    assert service.list_skills() == []

    try:
        service.uninstall("../route_helper")
    except SkillValidationError as exc:
        assert "unsafe skill id" in str(exc)
    else:
        raise AssertionError("expected unsafe skill id to be rejected")


def test_skill_management_service_imports_skill_markdown_url_with_fetcher(tmp_path):
    from app.agent.skills.management import SkillManagementService

    def fetcher(url: str) -> str:
        assert url == "https://example.test/SKILL.md"
        return """---
name: poi-helper
description: POI helper.
version: 1.0.0
metadata:
  openclaw:
    tools:
      - poi
---

# POI Helper

Use for place lookup.
"""

    service = SkillManagementService(tmp_path / "agent_skills", url_fetcher=fetcher)

    result = service.import_from_url("https://example.test/SKILL.md")
    skills = service.list_skills()

    assert result["skill_id"] == "poi_helper"
    assert skills[0]["install_report"]["allowed_tools"] == ["travel_search_poi"]


def test_skill_management_service_rejects_private_skill_url(tmp_path):
    from app.agent.skills.importer.validator import SkillValidationError
    from app.agent.skills.management import SkillManagementService

    service = SkillManagementService(tmp_path / "agent_skills", url_fetcher=lambda _url: "# never fetched")

    try:
        service.import_from_url("http://127.0.0.1/SKILL.md")
    except SkillValidationError as exc:
        assert "unsupported skill url host" in str(exc)
    else:
        raise AssertionError("expected private URL host to be rejected")


def test_agent_skills_router_exposes_management_endpoints():
    from app.api.v1.router import router

    routes = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}

    assert ("/api/v1/agent/skills", ("GET",)) in routes
    assert ("/api/v1/agent/skills/import/directory", ("POST",)) in routes
    assert ("/api/v1/agent/skills/import/zip", ("POST",)) in routes
    assert ("/api/v1/agent/skills/import/url", ("POST",)) in routes
    assert ("/api/v1/agent/skills/{skill_id}", ("DELETE",)) in routes

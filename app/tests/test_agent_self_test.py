from __future__ import annotations

from scripts.agent_self_test import select_pytest_command


def test_agent_self_test_uses_agent_test_python_for_pytest(monkeypatch):
    monkeypatch.delenv("PYTEST_BIN", raising=False)
    monkeypatch.setenv("AGENT_TEST_PYTHON", "/tmp/project-python")

    assert select_pytest_command() == ["/tmp/project-python", "-m", "pytest"]


def test_agent_self_test_prefers_explicit_pytest_bin(monkeypatch):
    monkeypatch.setenv("PYTEST_BIN", "/tmp/pytest -q")
    monkeypatch.setenv("AGENT_TEST_PYTHON", "/tmp/project-python")

    assert select_pytest_command() == ["/tmp/pytest", "-q"]

from pathlib import Path

from scripts.replay_eval import is_fallback, load_cases


def test_load_cases_from_fixture():
    cases = load_cases(Path("app/tests/fixtures/replay_cases.json"))
    assert isinstance(cases, list)
    assert len(cases) >= 3
    assert cases[0]["id"] == "eatout-location-as-target"


def test_is_fallback_detects_reason_fallback():
    assert is_fallback({"recommendations": [{"type": "note", "reason": "fallback"}]}) is True
    assert is_fallback({"recommendations": [{"type": "note", "reason": "ok"}]}) is False

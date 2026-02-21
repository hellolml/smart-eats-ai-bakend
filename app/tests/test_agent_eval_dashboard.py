from pathlib import Path

from scripts.agent_eval_dashboard import load_json


def test_load_json(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"a":1}', encoding="utf-8")
    data = load_json(p)
    assert data["a"] == 1

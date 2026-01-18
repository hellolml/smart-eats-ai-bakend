import json


def test_load_servers_from_file_missing_file(tmp_path):
    from app.infra.mcp.config import load_servers_from_file

    assert load_servers_from_file(str(tmp_path / "missing.json")) is None


def test_load_servers_from_file_expands_env_and_guesses_transport(monkeypatch, tmp_path):
    from app.infra.mcp.config import load_servers_from_file

    monkeypatch.setenv("AMAP_API_KEY", "k")
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        json.dumps({"amap": {"url": "https://mcp.amap.com/sse?key=${AMAP_API_KEY}"}}),
        encoding="utf-8",
    )
    servers = load_servers_from_file(str(config_path))
    assert servers["amap"]["url"].endswith("key=k")
    assert servers["amap"]["transport"] == "sse"

from app.agent import factory


class _Settings:
    def __init__(self, runtime: str):
        self.AGENT_GRAPH_RUNTIME = runtime


def test_build_agent_graph_uses_legacy_builder(monkeypatch):
    marker = {"called": None}

    def _legacy(**_kwargs):
        marker["called"] = "legacy"
        return "legacy_graph"

    def _official(**_kwargs):
        marker["called"] = "official"
        return "official_graph"

    monkeypatch.setattr(factory, "settings", _Settings("legacy"))

    import app.agent.graph as graph_module

    monkeypatch.setattr(graph_module, "build_langgraph", _legacy)
    monkeypatch.setattr(graph_module, "build_langgraph_official", _official)

    result = factory.build_agent_graph(
        db=None,
        redis_client=None,
        agent_config=None,
        provider=None,
    )

    assert result == "legacy_graph"
    assert marker["called"] == "legacy"


def test_build_agent_graph_uses_official_builder(monkeypatch):
    marker = {"called": None}

    def _legacy(**_kwargs):
        marker["called"] = "legacy"
        return "legacy_graph"

    def _official(**_kwargs):
        marker["called"] = "official"
        return "official_graph"

    monkeypatch.setattr(factory, "settings", _Settings("official"))

    import app.agent.graph as graph_module

    monkeypatch.setattr(graph_module, "build_langgraph", _legacy)
    monkeypatch.setattr(graph_module, "build_langgraph_official", _official)

    class _AgentConfig:
        name = "other_agent"

    result = factory.build_agent_graph(
        db=None,
        redis_client=None,
        agent_config=_AgentConfig(),
        provider=None,
    )

    assert result == "official_graph"
    assert marker["called"] == "official"


def test_build_agent_graph_uses_smart_eats_dedicated_builder_in_official_mode(monkeypatch):
    monkeypatch.setattr(factory, "settings", _Settings("official"))

    marker = {"called": None}

    def _smart(**_kwargs):
        marker["called"] = "smart"
        return "smart_graph"

    monkeypatch.setattr(factory, "build_smart_eats_graph", _smart)

    class _AgentConfig:
        name = "smart_eats"

    result = factory.build_agent_graph(
        db=None,
        redis_client=None,
        agent_config=_AgentConfig(),
        provider=None,
    )

    assert result == "smart_graph"
    assert marker["called"] == "smart"


def test_build_agent_graph_fallbacks_to_legacy_on_invalid_runtime(monkeypatch):
    marker = {"called": None}

    def _legacy(**_kwargs):
        marker["called"] = "legacy"
        return "legacy_graph"

    def _official(**_kwargs):
        marker["called"] = "official"
        return "official_graph"

    monkeypatch.setattr(factory, "settings", _Settings("invalid"))

    import app.agent.graph as graph_module

    monkeypatch.setattr(graph_module, "build_langgraph", _legacy)
    monkeypatch.setattr(graph_module, "build_langgraph_official", _official)

    result = factory.build_agent_graph(
        db=None,
        redis_client=None,
        agent_config=None,
        provider=None,
    )

    assert result == "legacy_graph"
    assert marker["called"] == "legacy"

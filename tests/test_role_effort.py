"""Deep (managers) and quick (analysts, debaters) roles can run at different effort levels."""

from types import SimpleNamespace

from tradingagents.graph.trading_graph import TradingAgentsGraph


def _kwargs(config, role=None):
    return TradingAgentsGraph._get_provider_kwargs(SimpleNamespace(config=config), role)


def test_role_effort_overrides_the_shared_codex_effort():
    config = {"llm_provider": "codex_subscription", "openai_reasoning_effort": "medium", "deep_think_effort": "high"}
    assert _kwargs(config, "deep")["reasoning_effort"] == "high"
    assert _kwargs(config, "quick")["reasoning_effort"] == "medium"


def test_role_effort_uses_the_claude_effort_key():
    config = {"llm_provider": "claude_subscription", "quick_think_effort": "medium"}
    assert _kwargs(config, "quick")["effort"] == "medium"
    assert "effort" not in _kwargs(config, "deep")


def test_role_effort_is_ignored_for_providers_without_an_effort_knob():
    config = {"llm_provider": "google", "deep_think_effort": "high"}
    assert "reasoning_effort" not in _kwargs(config, "deep")
    assert "effort" not in _kwargs(config, "deep")


def test_role_effort_can_be_set_from_the_environment(monkeypatch):
    import importlib

    import tradingagents.default_config as default_config

    monkeypatch.setenv("TRADINGAGENTS_DEEP_THINK_EFFORT", "high")
    monkeypatch.setenv("TRADINGAGENTS_QUICK_THINK_EFFORT", "low")
    reloaded = importlib.reload(default_config)
    try:
        assert reloaded.DEFAULT_CONFIG["deep_think_effort"] == "high"
        assert reloaded.DEFAULT_CONFIG["quick_think_effort"] == "low"
    finally:
        monkeypatch.delenv("TRADINGAGENTS_DEEP_THINK_EFFORT")
        monkeypatch.delenv("TRADINGAGENTS_QUICK_THINK_EFFORT")
        importlib.reload(default_config)

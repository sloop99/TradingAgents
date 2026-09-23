from __future__ import annotations

import json
import os
import subprocess
from enum import Enum

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tradingagents.llm_clients.claude_subscription_client import (
    ClaudeSubscriptionChatModel,
    ClaudeSubscriptionClient,
    resolve_claude_executable,
)
from tradingagents.llm_clients.factory import create_llm_client


class _Rating(str, Enum):
    BUY = "Buy"
    HOLD = "Hold"


class _Decision(BaseModel):
    rating: _Rating = Field(description="Pick exactly one supported rating")
    confidence: int


class _PriceArgs(BaseModel):
    ticker: str
    look_back_days: int = 30


def _fake_claude(monkeypatch, result: dict):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["prompt"] = kwargs["input"]
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        payload = {"type": "result", "subtype": "success", "is_error": False, **result}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return captured


def _flag(command, name):
    return command[command.index(name) + 1]


def test_factory_routes_claude_subscription_provider():
    client = create_llm_client("claude_subscription", "claude-opus-5-5")
    assert isinstance(client, ClaudeSubscriptionClient)
    assert isinstance(client.get_llm(), ClaudeSubscriptionChatModel)


def test_plain_invoke_runs_an_isolated_toolless_print_session(monkeypatch):
    captured = _fake_claude(monkeypatch, {"result": "final answer"})
    model = ClaudeSubscriptionChatModel(
        claude_executable="claude-test", model_name="claude-opus-5-5", reasoning_effort="high",
    )

    result = model.invoke("Analyze AAPL")

    assert result.content == "final answer"
    command = captured["command"]
    assert command[:2] == ["claude-test", "-p"]
    assert _flag(command, "--tools") == ""
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in command
    assert _flag(command, "--output-format") == "json"
    assert _flag(command, "--model") == "claude-opus-5-5"
    assert _flag(command, "--effort") == "high"
    assert "Analyze AAPL" in captured["prompt"]
    assert "Analyze AAPL" not in " ".join(command)


def test_default_model_name_leaves_the_cli_default(monkeypatch):
    captured = _fake_claude(monkeypatch, {"result": "ok"})
    ClaudeSubscriptionChatModel(claude_executable="claude-test").invoke("hi")
    assert "--model" not in captured["command"]
    assert "--effort" not in captured["command"]


def test_child_process_uses_the_subscription_not_an_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")
    captured = _fake_claude(monkeypatch, {"result": "ok"})
    ClaudeSubscriptionChatModel(claude_executable="claude-test").invoke("hi")
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "CLAUDECODE" not in captured["env"]
    assert captured["env"].get("PATH") == os.environ.get("PATH")


def test_structured_output_inlines_refs_and_returns_the_model(monkeypatch):
    captured = _fake_claude(
        monkeypatch,
        {"result": "", "structured_output": {"rating": "Hold", "confidence": 6}},
    )
    model = ClaudeSubscriptionChatModel(claude_executable="claude-test")

    decision = model.with_structured_output(_Decision).invoke("Decide")

    assert decision == _Decision(rating=_Rating.HOLD, confidence=6)
    schema = json.loads(_flag(captured["command"], "--json-schema"))
    assert "$defs" not in schema and "$ref" not in json.dumps(schema)
    assert schema["properties"]["rating"]["enum"] == ["Buy", "Hold"]


def test_bound_tools_return_langchain_tool_calls(monkeypatch):
    _fake_claude(
        monkeypatch,
        {"result": "", "structured_output": {"content": "", "tool_calls": [{"name": "price", "args": {"ticker": "AAPL"}}]}},
    )
    tool = StructuredTool.from_function(
        func=lambda ticker, look_back_days=30: "", name="price", description="Get prices", args_schema=_PriceArgs,
    )
    message = ClaudeSubscriptionChatModel(claude_executable="claude-test").bind_tools([tool]).invoke("Price AAPL")
    assert message.tool_calls[0]["name"] == "price"
    assert message.tool_calls[0]["args"] == {"ticker": "AAPL"}


def test_cli_error_results_raise_with_the_reason(monkeypatch):
    def run(command, **kwargs):
        payload = {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "usage limit reached"}
        return subprocess.CompletedProcess(command, 1, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="usage limit reached"):
        ClaudeSubscriptionChatModel(claude_executable="claude-test").invoke("hi")


def test_graph_forwards_effort_to_claude_subscription():
    from types import SimpleNamespace

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = {"llm_provider": "claude_subscription", "anthropic_effort": "xhigh"}
    assert TradingAgentsGraph._get_provider_kwargs(SimpleNamespace(config=config))["effort"] == "xhigh"


def test_client_passes_effort_to_the_chat_model():
    llm = ClaudeSubscriptionClient("claude-opus-5-5", effort="max").get_llm()
    assert llm.reasoning_effort == "max"
    assert llm.model_name == "claude-opus-5-5"


def test_explicit_claude_path_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(tmp_path / "claude.exe"))
    assert resolve_claude_executable() == str(tmp_path / "claude.exe")


def test_discovers_the_newest_vscode_extension_binary(tmp_path, monkeypatch):
    import tradingagents.llm_clients.claude_subscription_client as module

    extensions = tmp_path / ".vscode" / "extensions"
    older = extensions / "anthropic.claude-code-2.1.9-win32-x64" / "resources" / "native-binary" / "claude.exe"
    newer = extensions / "anthropic.claude-code-2.1.280-win32-x64" / "resources" / "native-binary" / "claude.exe"
    for path in (older, newer):
        path.parent.mkdir(parents=True)
        path.write_text("")
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    assert resolve_claude_executable() == str(newer)

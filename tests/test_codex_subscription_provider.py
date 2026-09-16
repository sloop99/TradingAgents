from __future__ import annotations

import json
import subprocess
from enum import Enum
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tradingagents.llm_clients.codex_subscription_client import (
    CodexSubscriptionChatModel,
    CodexSubscriptionClient,
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


def _fake_codex(monkeypatch, payload):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["prompt"] = kwargs["input"]
        if "--output-schema" in command:
            schema_path = Path(command[command.index("--output-schema") + 1])
            captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return captured


def test_factory_routes_subscription_provider():
    client = create_llm_client("codex_subscription", "default")
    assert isinstance(client, CodexSubscriptionClient)
    assert isinstance(client.get_llm(), CodexSubscriptionChatModel)


def test_plain_invoke_uses_isolated_noninteractive_exec(monkeypatch):
    captured = _fake_codex(monkeypatch, "final answer")
    model = CodexSubscriptionChatModel(codex_executable="codex-test")

    result = model.invoke("Analyze AAPL")

    assert result.content == "final answer"
    command = captured["command"]
    assert command[:2] == ["codex-test", "exec"]
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "Analyze AAPL" in captured["prompt"]


def test_bound_tools_return_langchain_tool_calls(monkeypatch):
    captured = _fake_codex(
        monkeypatch,
        {"content": "", "tool_calls": [{"name": "price", "args": {"ticker": "AAPL"}}]},
    )
    tool = StructuredTool.from_function(
        func=lambda ticker, look_back_days=30: "123",
        name="price",
        description="Look up a ticker price",
        args_schema=_PriceArgs,
    )
    model = CodexSubscriptionChatModel().bind_tools([tool])

    result = model.invoke("Get the AAPL price")

    assert result.content == ""
    assert result.tool_calls[0]["name"] == "price"
    assert result.tool_calls[0]["args"] == {"ticker": "AAPL"}
    assert "APPLICATION TOOLS" in captured["prompt"]
    assert "--output-schema" in captured["command"]
    tool_call_schema = captured["schema"]["properties"]["tool_calls"]["items"]["anyOf"][0]
    assert tool_call_schema["additionalProperties"] is False
    assert tool_call_schema["properties"]["args"]["additionalProperties"] is False
    assert "ticker" in tool_call_schema["properties"]["args"]["properties"]
    assert tool_call_schema["properties"]["args"]["required"] == [
        "ticker",
        "look_back_days",
    ]


def test_structured_output_returns_pydantic_model(monkeypatch):
    captured = _fake_codex(monkeypatch, {"rating": "Buy", "confidence": 9})
    model = CodexSubscriptionChatModel(model_name="gpt-test", reasoning_effort="high")

    result = model.with_structured_output(_Decision).invoke("Make a decision")

    assert result == _Decision(rating="Buy", confidence=9)
    command = captured["command"]
    assert command[command.index("--model") + 1] == "gpt-test"
    assert 'model_reasoning_effort="high"' in command
    schema_path = Path(command[command.index("--output-schema") + 1])
    # The temporary directory is gone after invocation, but the flag confirms
    # the CLI received a schema path and the mocked response was model-validated.
    assert schema_path.name == "schema.json"
    assert captured["schema"]["additionalProperties"] is False
    assert captured["schema"]["properties"]["rating"] == {
        "$ref": "#/$defs/_Rating"
    }

"""LangChain chat-model adapter for a ChatGPT-authenticated Codex CLI.

This provider is intentionally separate from the OpenAI API client.  It runs
``codex exec`` as a local subprocess, so authentication and usage come from the
user's Codex/ChatGPT subscription rather than ``OPENAI_API_KEY``.  The stable
non-interactive CLI supports both final-message capture and JSON Schema output;
the adapter uses those features to expose the ``BaseChatModel`` methods the
TradingAgents graph expects.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field

from .base_client import BaseLLMClient

_DEFAULT_MODEL_NAMES = {"", "default", "codex-default", "subscription-default"}


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy that satisfies Codex/OpenAI strict object-schema rules."""
    normalized = json.loads(json.dumps(schema))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            # Pydantic emits field descriptions beside local enum ``$ref``
            # entries. Codex's strict schema validator rejects every sibling
            # keyword on a reference, so retain the reference itself.
            if "$ref" in node and len(node) > 1:
                reference = node["$ref"]
                node.clear()
                node["$ref"] = reference
                return
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                properties = node.get("properties", {})
                node["required"] = list(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(normalized)
    return normalized


def _message_content(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _coerce_messages(value: Any) -> list[BaseMessage]:
    if hasattr(value, "to_messages"):
        return list(value.to_messages())
    if isinstance(value, str):
        return list(convert_to_messages([("human", value)]))
    if isinstance(value, Sequence):
        return list(convert_to_messages(value))
    return list(convert_to_messages([("human", str(value))]))


class CodexSubscriptionChatModel(BaseChatModel):
    """Chat model implemented by isolated ``codex exec`` invocations."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str = "default"
    codex_executable: str = "codex"
    timeout_seconds: float = 600.0
    reasoning_effort: str | None = None
    bound_tools: tuple[dict[str, Any], ...] = Field(default_factory=tuple)

    @property
    def _llm_type(self) -> str:
        return "codex-subscription-cli"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "codex_executable": self.codex_executable,
        }

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> CodexSubscriptionChatModel:
        """Return a copy that asks Codex for a LangChain-compatible tool envelope."""
        del tool_choice, kwargs
        converted = tuple(convert_to_openai_tool(tool) for tool in tools)
        return self.model_copy(update={"bound_tools": converted})

    def with_structured_output(
        self,
        schema: dict[str, Any] | type[BaseModel],
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ):
        """Use ``codex exec --output-schema`` and parse the validated result."""
        del kwargs

        def invoke(value: Any):
            messages = _coerce_messages(value)
            try:
                parsed = self._invoke_schema(messages, schema)
                if include_raw:
                    return {"raw": None, "parsed": parsed, "parsing_error": None}
                return parsed
            except Exception as exc:
                if include_raw:
                    return {"raw": None, "parsed": None, "parsing_error": exc}
                raise

        return RunnableLambda(invoke)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        if self.bound_tools:
            payload = self._invoke_tools(messages)
            tool_calls = [
                {
                    "name": call["name"],
                    "args": call.get("args", {}),
                    "id": f"call_{uuid.uuid4().hex[:16]}",
                    "type": "tool_call",
                }
                for call in payload.get("tool_calls", [])
            ]
            message = AIMessage(content=payload.get("content", ""), tool_calls=tool_calls)
        else:
            message = AIMessage(content=self._complete(self._build_prompt(messages)))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _invoke_tools(self, messages: list[BaseMessage]) -> dict[str, Any]:
        tool_call_variants = []
        for tool in self.bound_tools:
            function = tool["function"]
            tool_call_variants.append(
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": [function["name"]]},
                        "args": function.get(
                            "parameters",
                            {"type": "object", "properties": {}},
                        ),
                    },
                    "required": ["name", "args"],
                    "additionalProperties": False,
                }
            )
        schema = {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tool_calls": {
                    "type": "array",
                    "items": {"anyOf": tool_call_variants},
                },
            },
            "required": ["content", "tool_calls"],
            "additionalProperties": False,
        }
        tool_text = json.dumps(self.bound_tools, ensure_ascii=False, indent=2)
        instructions = (
            "The application exposes the tools below. Do not execute any built-in tools. "
            "If the transcript requires missing data, return the necessary application tool "
            "calls with exact names and JSON arguments, and set content to an empty string. "
            "If tool results are already present and sufficient, return the final answer in "
            "content with an empty tool_calls array.\n\nAPPLICATION TOOLS:\n"
            + tool_text
        )
        raw = self._complete(self._build_prompt(messages, instructions), schema)
        return json.loads(raw)

    def _invoke_schema(
        self,
        messages: list[BaseMessage],
        schema: dict[str, Any] | type[BaseModel],
    ) -> Any:
        is_model = isinstance(schema, type) and issubclass(schema, BaseModel)
        if is_model:
            json_schema = schema.model_json_schema()
        elif isinstance(schema, dict) and "function" in schema:
            json_schema = schema["function"]["parameters"]
        else:
            json_schema = schema

        instructions = (
            "Return only an instance of the requested response schema. Do not use any "
            "built-in tools, inspect files, or browse the web. Use only the transcript."
        )
        raw = self._complete(self._build_prompt(messages, instructions), json_schema)
        payload = json.loads(raw)
        return schema.model_validate(payload) if is_model else payload

    def _build_prompt(
        self,
        messages: list[BaseMessage],
        extra_instructions: str = "",
    ) -> str:
        transcript: list[str] = []
        for message in messages:
            role = message.type.upper()
            label = role
            if role == "TOOL":
                label += f" name={getattr(message, 'name', '')}"
            transcript.append(f"[{label}]\n{_message_content(message)}")
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                transcript.append(
                    "[ASSISTANT TOOL CALLS]\n"
                    + json.dumps(tool_calls, ensure_ascii=False, default=str)
                )

        header = (
            "Act only as a stateless language-model inference backend for another local "
            "application. Do not inspect the filesystem, run shell commands, modify files, "
            "or use web search. Follow the message transcript and return the requested answer "
            "directly.\n"
        )
        if extra_instructions:
            header += "\n" + extra_instructions + "\n"
        return header + "\nMESSAGE TRANSCRIPT:\n\n" + "\n\n".join(transcript)

    def _complete(self, prompt: str, output_schema: dict[str, Any] | None = None) -> str:
        """Run one completion; subclasses swap in a different subscription CLI."""
        return self._run_codex(prompt, output_schema)

    def _run_codex(self, prompt: str, output_schema: dict[str, Any] | None = None) -> str:
        executable = shutil.which(self.codex_executable) or self.codex_executable
        with tempfile.TemporaryDirectory(prefix="tradingagents-codex-") as temp_dir:
            temp = Path(temp_dir)
            output_path = temp / "response.txt"
            command = [
                executable,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--cd",
                str(temp),
                "--output-last-message",
                str(output_path),
            ]
            if self.model_name.lower() not in _DEFAULT_MODEL_NAMES:
                command.extend(["--model", self.model_name])
            if self.reasoning_effort:
                command.extend([
                    "--config",
                    f'model_reasoning_effort="{self.reasoning_effort}"',
                ])
            if output_schema is not None:
                schema_path = temp / "schema.json"
                schema_path.write_text(
                    json.dumps(_strict_json_schema(output_schema), ensure_ascii=False),
                    encoding="utf-8",
                )
                command.extend(["--output-schema", str(schema_path)])
            command.append("-")

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                creationflags=creationflags,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "unknown Codex CLI error").strip()
                raise RuntimeError(f"Codex CLI failed ({completed.returncode}): {detail[-2000:]}")
            if not output_path.exists():
                raise RuntimeError("Codex CLI completed without writing its final response")
            return output_path.read_text(encoding="utf-8").strip()


def resolve_codex_executable() -> str:
    """CODEX_CLI_PATH, then ``codex`` on PATH, then the newest Codex desktop-app binary.

    The Codex app installs its CLI under a versioned folder that is not added
    to PATH, so shells outside the app would otherwise fail to find it.
    """
    explicit = os.environ.get("CODEX_CLI_PATH")
    if explicit:
        return explicit
    on_path = shutil.which("codex")
    if on_path:
        return on_path
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        installed = sorted(
            Path(local_app_data).glob("OpenAI/Codex/bin/*/codex.exe"),
            key=lambda path: path.stat().st_mtime,
        )
        if installed:
            return str(installed[-1])
    return "codex"


class CodexSubscriptionClient(BaseLLMClient):
    """Factory wrapper for the subscription-authenticated Codex CLI model."""

    def get_llm(self) -> CodexSubscriptionChatModel:
        executable = resolve_codex_executable()
        timeout = float(os.environ.get("CODEX_CLI_TIMEOUT", self.kwargs.get("timeout", 600)))
        callbacks = self.kwargs.get("callbacks")
        return CodexSubscriptionChatModel(
            model_name=self.model,
            codex_executable=executable,
            timeout_seconds=timeout,
            reasoning_effort=self.kwargs.get("reasoning_effort"),
            callbacks=callbacks,
        )

    def validate_model(self) -> bool:
        # Subscription model availability is determined by the signed-in Codex
        # account and changes independently of this package's API model catalog.
        return True

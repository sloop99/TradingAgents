"""LangChain chat-model adapter for a Claude-subscription-authenticated Claude Code CLI.

The Claude counterpart of ``codex_subscription``: each completion runs the
official ``claude`` CLI in print mode as a local subprocess, so authentication
and usage come from the user's own Claude Pro/Max login rather than an API key.
Every call is isolated: no tools, no hooks, skills, plugins, MCP servers or
CLAUDE.md files (``--safe-mode``), a replaced system prompt, and no saved
session. Structured output uses the CLI's ``--json-schema`` support. Tool
calling and message rendering are shared with the Codex adapter.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base_client import BaseLLMClient
from .codex_subscription_client import (
    _DEFAULT_MODEL_NAMES,
    CodexSubscriptionChatModel,
    _strict_json_schema,
)

_SYSTEM_PROMPT = (
    "You are a stateless language-model inference backend for a local research "
    "application. Answer the request in the user message directly."
)
# Credentials that would switch the CLI from the subscription to API billing, and
# markers a parent Claude Code session sets that do not belong to the child.
_STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
_EXTENSION_VERSION = re.compile(r"anthropic\.claude-code-(\d+(?:\.\d+)*)")


def resolve_claude_executable() -> str:
    """CLAUDE_CLI_PATH, then the native installer's binary, then the newest VS Code
    extension binary, then ``claude`` on PATH.

    The npm shim on PATH can point at a broken or wrong-platform binary, so the
    self-contained native binaries are preferred when present.
    """
    explicit = os.environ.get("CLAUDE_CLI_PATH")
    if explicit:
        return explicit
    home = Path.home()
    native = home / ".local" / "bin" / "claude.exe"
    if native.is_file():
        return str(native)

    def version(path: Path) -> tuple[int, ...]:
        match = _EXTENSION_VERSION.search(path.as_posix())
        return tuple(int(part) for part in match.group(1).split(".")) if match else ()

    bundled = sorted(
        (home / ".vscode" / "extensions").glob("anthropic.claude-code-*/resources/native-binary/claude.exe"),
        key=version,
    )
    if bundled:
        return str(bundled[-1])
    return shutil.which("claude") or "claude"


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace local ``$ref``s with their ``$defs`` targets for the CLI's draft-07 validator."""
    definitions = {**schema.get("definitions", {}), **schema.get("$defs", {})}

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 32:
            raise ValueError("JSON schema references are nested too deeply (recursive schema?)")
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/"):
                target = definitions.get(reference.rsplit("/", 1)[-1])
                if target is not None:
                    siblings = {key: value for key, value in node.items() if key != "$ref"}
                    return {**resolve(target, depth + 1), **resolve(siblings, depth + 1)}
            return {
                key: resolve(value, depth + 1)
                for key, value in node.items()
                if key not in ("$defs", "definitions")
            }
        if isinstance(node, list):
            return [resolve(value, depth + 1) for value in node]
        return node

    return resolve(schema)


class ClaudeSubscriptionChatModel(CodexSubscriptionChatModel):
    """Chat model implemented by isolated ``claude -p`` invocations."""

    claude_executable: str = "claude"
    timeout_seconds: float = 900.0

    @property
    def _llm_type(self) -> str:
        return "claude-subscription-cli"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name, "claude_executable": self.claude_executable}

    def _complete(self, prompt: str, output_schema: dict[str, Any] | None = None) -> str:
        executable = shutil.which(self.claude_executable) or self.claude_executable
        command = [
            executable,
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--system-prompt",
            _SYSTEM_PROMPT,
        ]
        if self.model_name.lower() not in _DEFAULT_MODEL_NAMES:
            command.extend(["--model", self.model_name])
        if self.reasoning_effort:
            command.extend(["--effort", self.reasoning_effort])
        if output_schema is not None:
            schema = _inline_refs(_strict_json_schema(output_schema))
            command.extend(["--json-schema", json.dumps(schema, ensure_ascii=False)])

        env = {key: value for key, value in os.environ.items() if key not in _STRIPPED_ENV}
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with tempfile.TemporaryDirectory(prefix="tradingagents-claude-") as temp_dir:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                cwd=temp_dir,
                env=env,
                creationflags=creationflags,
            )
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            detail = (completed.stderr or completed.stdout or "no output").strip()
            raise RuntimeError(f"Claude CLI failed ({completed.returncode}): {detail[-2000:]}") from None
        if completed.returncode != 0 or payload.get("is_error") or payload.get("subtype") != "success":
            reason = payload.get("result") or payload.get("subtype") or completed.stderr or "unknown error"
            raise RuntimeError(f"Claude CLI failed ({completed.returncode}): {str(reason).strip()[-2000:]}")
        if output_schema is not None and payload.get("structured_output") is not None:
            return json.dumps(payload["structured_output"], ensure_ascii=False)
        return str(payload.get("result", "")).strip()


class ClaudeSubscriptionClient(BaseLLMClient):
    """Factory wrapper for the subscription-authenticated Claude Code CLI model."""

    def get_llm(self) -> ClaudeSubscriptionChatModel:
        timeout = float(os.environ.get("CLAUDE_CLI_TIMEOUT", self.kwargs.get("timeout", 900)))
        return ClaudeSubscriptionChatModel(
            model_name=self.model,
            claude_executable=resolve_claude_executable(),
            timeout_seconds=timeout,
            reasoning_effort=self.kwargs.get("effort"),
            callbacks=self.kwargs.get("callbacks"),
        )

    def validate_model(self) -> bool:
        # Which models the signed-in Claude account can use is decided by the CLI.
        return True

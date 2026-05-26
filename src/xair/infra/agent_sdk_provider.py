"""LlmProvider implementation -- Claude Agent SDK (Claude Code headless).

Runs Claude as a full agent with codebase access (Read, Glob, Grep).
This is the ONLY way Claude is used in XAIR — raw Anthropic API is
never called. Claude without tools is blind.

Falls back gracefully: if claude-agent-sdk is not installed, Container
disables deep analysis (GPT-only mode).
"""

from __future__ import annotations

import asyncio
import json
import os

from ..domain.exceptions import ConfigError, LlmError
from ..log import logger

_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TURNS = 30
_DEEP_TOOLS = ["Read", "Glob", "Grep"]

# JSON Schema enforced via the Agent SDK's StructuredOutput mechanism.
# The CLI validates the agent's final response against this schema and
# retries automatically if it doesn't match — no prompt-level "Return
# ONLY valid JSON" needed.
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "type": {"type": "string"},
                    "observation": {"type": "string"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "string"},
                    "related_files": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "file",
                    "line",
                    "type",
                    "observation",
                    "evidence",
                    "confidence",
                ],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["observations", "summary"],
}

# Prepended to the system prompt so Claude uses its tools instead of
# analyzing the diff blindly. The base prompt (from deep-analysis.md)
# provides the output schema and analysis instructions.
_AGENT_PREAMBLE = """\
IMPORTANT: You have access to the full repository via Read, Glob, and Grep \
tools. USE THEM. Do not guess what code does from the diff alone — read the \
actual source files, trace call chains with Grep, and verify invariants \
against the real codebase.

"""


class AgentSDKProvider:
    """Runs Claude as an agent with repo access via the Agent SDK.

    Implements the LlmProvider protocol (structural subtyping).
    The agent can Read files, Glob for patterns, and Grep for content
    while formulating its analysis -- producing observations grounded
    in actual code, not just diff context.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        cwd: str | None = None,
        max_turns: int = _DEFAULT_MAX_TURNS,
    ) -> None:
        self._model = model
        self._cwd = cwd or os.environ.get(
            "TARGET_REPO_PATH",
            os.environ.get("GITHUB_WORKSPACE", "."),
        )
        self._max_turns = max_turns
        self._last_usage: dict = {}

    @property
    def last_usage(self) -> dict:
        """Token usage from the most recent call."""
        return self._last_usage

    def call(  # noqa: ARG002 — max_tokens/temperature/json_mode required by LlmProvider protocol
        self,
        *,
        system: str,
        user: str,
        model: str = "",
        max_tokens: int = 4096,  # noqa: ARG002
        temperature: float = 0.1,  # noqa: ARG002
        json_mode: bool = True,  # noqa: ARG002
    ) -> dict:
        try:
            from claude_agent_sdk import (
                ClaudeAgentOptions,
                ResultMessage,
                AssistantMessage,
                ToolUseBlock,
                query,
            )
        except ImportError:
            raise ConfigError(
                "claude-agent-sdk is not installed. "
                "Run: pip install claude-agent-sdk"
            )

        resolved_model = model or self._model
        agent_system = _AGENT_PREAMBLE + system

        options = ClaudeAgentOptions(
            cwd=self._cwd,
            allowed_tools=_DEEP_TOOLS,
            permission_mode="bypassPermissions",
            system_prompt=agent_system,
            model=resolved_model,
            max_turns=self._max_turns,
            output_format={"type": "json_schema", "schema": _OUTPUT_SCHEMA},
        )

        structured_result: dict | None = None
        result_text = ""
        result_subtype = ""
        total_usage: dict = {"input_tokens": 0, "output_tokens": 0}
        tool_calls = 0
        turns = 0

        logger.debug(f"  Agent SDK | cwd={self._cwd}")
        logger.debug(f"  Agent SDK | model={resolved_model}, max_turns={self._max_turns}")
        logger.debug(f"  Agent SDK | output_format=json_schema (enforced)")
        logger.debug(f"  Agent SDK | prompt={len(user)} chars, system={len(agent_system)} chars")

        async def _run() -> None:
            nonlocal structured_result, result_text, result_subtype
            nonlocal total_usage, tool_calls, turns
            async for message in query(prompt=user, options=options):
                if isinstance(message, AssistantMessage):
                    turns += 1
                    if message.usage:
                        total_usage["input_tokens"] += message.usage.get(
                            "input_tokens", 0
                        )
                        total_usage["output_tokens"] += message.usage.get(
                            "output_tokens", 0
                        )
                    for block in getattr(message, "content", []):
                        if isinstance(block, ToolUseBlock):
                            tool_calls += 1
                            name = block.name
                            inp = block.input or {}
                            if name == "Read":
                                logger.debug(f"  Agent SDK | Read: {inp.get('file_path', '?')}")
                            elif name == "Grep":
                                logger.debug(f"  Agent SDK | Grep: {inp.get('pattern', '?')}")
                            elif name == "Glob":
                                logger.debug(f"  Agent SDK | Glob: {inp.get('pattern', '?')}")
                            else:
                                logger.debug(f"  Agent SDK | {name}: {str(inp)[:80]}")
                if isinstance(message, ResultMessage):
                    result_text = message.result or ""
                    result_subtype = getattr(message, "subtype", "") or ""
                    structured_result = message.structured_output

        try:
            asyncio.run(_run())
        except Exception as e:
            logger.debug(f"  Agent SDK | EXCEPTION: {type(e).__name__}: {e}")
            raise LlmError(
                f"Agent SDK failed: {e}",
                status_code=0,
                body=str(e),
            )

        self._last_usage = total_usage
        it = total_usage.get("input_tokens", 0)
        ot = total_usage.get("output_tokens", 0)
        logger.debug(f"  Agent SDK | {turns} turns, {tool_calls} tool calls, {it}+{ot} tokens")
        logger.debug(f"  Agent SDK | subtype={result_subtype}")

        if tool_calls == 0:
            logger.debug("  Agent SDK | WARNING: zero tool calls — agent did not explore the repo")

        # Primary path: structured_output from the SDK's StructuredOutput tool.
        # This is schema-validated by the CLI before we ever see it.
        if structured_result and isinstance(structured_result, dict):
            obs_count = len(structured_result.get("observations", []))
            logger.debug(f"  Agent SDK | structured_output: {obs_count} observations (schema-validated)")
            return structured_result

        # Fallback: the SDK hit error_max_structured_output_retries or
        # structured_output was None. Try parsing the raw result text.
        if result_subtype == "error_max_structured_output_retries":
            logger.debug("  Agent SDK | WARNING: structured output retries exhausted, trying raw text")

        logger.debug(f"  Agent SDK | result_text={len(result_text)} chars, falling back to text parse")

        if not result_text:
            return {"observations": [], "summary": "Agent returned no output"}

        import re

        stripped = result_text.strip()
        parsed = _try_parse_json(stripped)
        if parsed is not None:
            obs_count = len(parsed.get("observations", []))
            logger.debug(f"  Agent SDK | fallback parsed JSON: {obs_count} observations")
            return parsed

        fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", stripped, re.DOTALL)
        if fence_match:
            parsed = _try_parse_json(fence_match.group(1).strip())
            if parsed is not None:
                obs_count = len(parsed.get("observations", []))
                logger.debug(f"  Agent SDK | fallback parsed JSON from fences: {obs_count} observations")
                return parsed

        logger.debug(f"  Agent SDK | WARNING: no valid JSON in fallback")
        logger.debug(f"  Agent SDK | result preview: {result_text[:300]}")
        return {"_raw": result_text}


def _try_parse_json(text: str) -> dict | None:
    """Try to parse text as JSON, return None on failure."""
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None

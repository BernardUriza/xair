"""CodexCLIAgentRunner — implementación de AgentRunner Protocol sobre Codex CLI.

Implementa el adversarial-pair counterpart a ClaudeSDKAgentRunner. Donde Claude
usa el async ``query()`` del SDK, Codex usa el binary CLI como subprocess
sincrónico — más simple en términos de invocación, distinto en streaming.

## Codex CLI shape — assumed (configurable)

El binary del Codex CLI no está estandarizado entre versiones. Esta clase
asume el shape más común para agent CLIs:

- Invocación: ``codex --model <m> --max-turns <n> --json-stream``
- System prompt pasado vía ``--system-prompt`` (o stdin si la versión lo
  requiere)
- User prompt pasado por stdin
- Output: JSON Lines (cada línea es un evento ``{"type": ..., ...}``)
  - ``assistant_message`` events incrementan ``turns``
  - ``tool_use`` events incrementan ``tool_calls`` (y ``edit_calls`` si name
    es ``edit``/``write``)
  - ``result`` events contienen ``text`` (el resultado final)
- Exit code 0 = success, != 0 = error (stderr tiene detalles)

**Si el binary real expone otro shape**, ajustá ``_build_cmd`` y
``_parse_event`` — el resto de la clase (env handling, destructive detection,
outcome construction) sigue siendo válido.

## Diferencias vs ClaudeSDKAgentRunner

| Aspecto | Claude SDK | Codex CLI |
|---|---|---|
| Invocación | ``claude_agent_sdk.query()`` async | subprocess sincrónico |
| Auth env var | ``ANTHROPIC_API_KEY`` (removido en local) | ``OPENAI_API_KEY`` (siempre presente) |
| Output | message objects | JSON Lines en stdout |
| Tool names | ``Edit``/``Write``/``Bash`` (PascalCase) | ``edit``/``write``/``bash`` (lowercase) |

Ambos implementan el mismo ``AgentRunner`` Protocol — el caller no distingue.

## Por qué Codex (gpt-5.5) en el adversarial flow

Plan v7 sección 2: heterogeneidad de vendor (OpenAI vs Anthropic) es lo que
hace funcionar el adversarial. Mismo modelo o mismo vendor = blind spots
compartidos (arxiv 2502.08788). Codex aporta la perspectiva GPT; Claude
synthesizer valida desde un training distinto.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from ..domain.agent_run import DESTRUCTIVE_COMMAND_PATTERNS, AgentRunOutcome
from .constants import OPENAI_MODEL
from ..log import logger


# Codex tool names are lowercase by convention (vs Claude SDK's PascalCase)
_CODEX_EDIT_TOOLS = frozenset({"edit", "write"})


class CodexCLIAgentRunner:
    """AgentRunner backed by the Codex CLI binary (gpt-5.5).

    Synchronous wrapper. Invokes ``codex`` subprocess, parses JSON Lines
    stdout, and assembles an AgentRunOutcome. Errors (binary missing,
    timeout, non-zero exit) are captured as ``outcome.error`` instead of
    raising — same Protocol semantics as ClaudeSDKAgentRunner.
    """

    def __init__(
        self,
        *,
        model: str = OPENAI_MODEL,
        binary: str = "codex",
        allowed_tools: tuple[str, ...] = ("read", "edit", "bash", "write", "grep", "glob"),
        destructive_patterns: tuple[str, ...] = DESTRUCTIVE_COMMAND_PATTERNS,
        timeout_seconds: int = 300,
    ) -> None:
        self._model = model
        self._binary = binary
        self._allowed_tools = list(allowed_tools)
        self._destructive_patterns = destructive_patterns
        self._timeout = timeout_seconds

    def run(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        cwd: str,
        max_turns: int,
    ) -> AgentRunOutcome:
        """Invoca el Codex CLI y devuelve AgentRunOutcome.

        Mismo contrato que ClaudeSDKAgentRunner.run — síncrono desde el
        caller, errors capturados como ``outcome.error``.
        """
        # Resolve binary path eagerly. On Windows, subprocess.run with a bare
        # "codex" arg does NOT auto-resolve "codex.CMD" — it raises
        # FileNotFoundError even when shutil.which finds it. Resolving here
        # makes the runner cross-platform without shell=True (security).
        resolved_binary = shutil.which(self._binary) if not _looks_like_path(self._binary) else self._binary
        if not resolved_binary:
            error = (
                f"Codex CLI binary not found at {self._binary!r}. "
                "Install it or pass binary=<path> to the runner."
            )
            logger.error(error)
            return AgentRunOutcome(error=error)

        cmd = self._build_cmd(system_prompt=system_prompt, max_turns=max_turns)
        # First element of cmd is the binary name — replace with resolved path
        cmd = [resolved_binary] + cmd[1:]

        # Codex CLI ≥0.125 has no --system-prompt flag — prepend it to the
        # user prompt instead. Empty system_prompt: pass user_prompt as-is.
        full_prompt = (
            f"{system_prompt}\n\n---\n\n{user_prompt}" if system_prompt else user_prompt
        )

        # OPENAI_API_KEY: assumed always present (no env var dance needed,
        # unlike Claude SDK which spawns a CLI with its own OAuth chain).
        # If the Codex binary uses a different auth env var (e.g. CODEX_KEY),
        # adjust by passing env= to subprocess.run.

        try:
            proc = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=self._timeout,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            # Resolved path existed at check time but vanished — race or perms.
            error = (
                f"Codex CLI binary at {resolved_binary!r} disappeared between "
                "resolution and execution (race condition or permissions)."
            )
            logger.error(error)
            return AgentRunOutcome(error=error)
        except subprocess.TimeoutExpired:
            error = f"Codex CLI timed out after {self._timeout}s"
            logger.error(error)
            return AgentRunOutcome(error=error)

        # Parse Codex CLI ≥0.125 event stream. Real event shapes:
        # - {"type":"turn.started"}              → turns += 1
        # - {"type":"turn.completed", "usage":...} → end of turn
        # - {"type":"item.started", "item":{"type":"command_execution","command":...}}
        #     → tool_call (shell). Tracked by item.id to avoid double-count
        #       across started/completed pairs.
        # - {"type":"item.completed", "item":{"type":"agent_message","text":...}}
        #     → assistant output. Latest message wins (Codex emits one
        #       final agent_message per turn).
        # - {"type":"thread.started" / "thread.*"} → ignored

        result_text = ""
        turns = 0
        tool_calls = 0
        edit_calls = 0
        destructive_calls: list[str] = []
        counted_tool_items: set[str] = set()

        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            event = self._parse_event(line)
            if event is None:
                continue  # non-JSON output (banner, progress lines, etc.)

            event_type = event.get("type", "")

            if event_type == "turn.started":
                turns += 1
                continue

            if event_type in ("item.started", "item.completed"):
                item = event.get("item") or {}
                item_type = item.get("type", "")
                item_id = item.get("id", "")

                if item_type == "command_execution":
                    if item_id and item_id not in counted_tool_items:
                        counted_tool_items.add(item_id)
                        tool_calls += 1
                        cmd_str = item.get("command", "")
                        logger.info(f"  [codex][tool] command: {cmd_str[:200]}")
                        if cmd_str and any(
                            pat in cmd_str for pat in self._destructive_patterns
                        ):
                            destructive_calls.append(cmd_str[:500])
                            logger.warning(
                                f"  [codex][DESTRUCTIVE] {cmd_str[:200]}"
                            )

                elif item_type == "agent_message" and event_type == "item.completed":
                    # Take the final text from the last agent_message in the run
                    text = item.get("text", "")
                    if text:
                        result_text = text

        # Non-zero exit + no result = failure
        if proc.returncode != 0 and not result_text:
            error = (
                f"Codex CLI exited with code {proc.returncode}. "
                f"stderr: {(proc.stderr or '').strip()[:500]}"
            )
            logger.error(error)
            return AgentRunOutcome(
                result_text=result_text,
                turns=turns,
                tool_calls=tool_calls,
                edit_calls=edit_calls,
                destructive_calls=tuple(destructive_calls),
                error=error,
            )

        return AgentRunOutcome(
            result_text=result_text,
            turns=turns,
            tool_calls=tool_calls,
            edit_calls=edit_calls,
            destructive_calls=tuple(destructive_calls),
            error=None,
        )

    # -- Subclassing hooks -------------------------------------------------
    #
    # If the installed Codex CLI uses a different argv shape or output format,
    # subclass and override these two methods instead of forking the class.

    def _build_cmd(self, *, system_prompt: str, max_turns: int) -> list[str]:
        """Build the subprocess argv for Codex CLI ≥ 0.125.

        Real Codex CLI 0.125.0 shape (verified locally 2026-05-11):

            codex exec --json --model <m> --skip-git-repo-check \
                       --dangerously-bypass-approvals-and-sandbox

        Notes vs. the original assumed shape:
        - ``--max-turns`` does NOT exist. Turn budget is internal to the
          agent; we expose ``max_turns`` on the runner for parity with
          ``ClaudeSDKAgentRunner`` but cannot enforce it from the CLI.
          The agent will run until completion or until OpenAI rate limits.
        - ``--system-prompt`` does NOT exist as a flag. We prepend the
          system prompt to the user prompt with a delimiter — the model
          treats it as instructions in the same context.
        - ``--allow-tool`` does NOT exist. Tools are gated by sandbox
          policy. ``--dangerously-bypass-approvals-and-sandbox`` is used
          for non-interactive review runs (read-only intent enforced by
          prompt + post-run inspection of destructive_calls).

        Override this method in a subclass for newer CLI versions if the
        shape changes again.
        """
        # max_turns kept in signature for AgentRunner Protocol parity; the
        # real CLI ignores it. Same for allowed_tools at the moment.
        del system_prompt, max_turns  # consumed via prompt-prefix in run()

        return [
            self._binary,
            "exec",
            "--json",
            "--model",
            self._model,
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]

    def _parse_event(self, line: str) -> dict | None:
        """Parse a stdout line as a JSON event. Return None to skip the line.

        Override for non-JSON-Lines output formats.
        """
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None


def _looks_like_path(s: str) -> bool:
    r"""True if the string looks like a filesystem path (vs a bare command name).

    Bare names like ``codex`` go through ``shutil.which``; absolute or
    relative paths (``/usr/bin/codex``, ``./codex``, ``C:\bin\codex.exe``)
    are passed through to subprocess unchanged.
    """
    return "/" in s or "\\" in s or s.startswith(".")

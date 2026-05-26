"""ClaudeSDKAgentRunner — implementación de AgentRunner Protocol sobre
`claude_agent_sdk.query()`.

Encapsula tres responsabilidades que vivían inline en `run_resolve`:

1. **Async invocation**: `claude_agent_sdk.query()` es un async generator. El
   runner lo envuelve con `asyncio.run()` para presentar interfaz síncrona.
2. **ANTHROPIC_API_KEY env var dance**: el SDK spawn-ea el CLI `claude`, que
   usa el env var para auth directa. Localmente (sin GITHUB_ACTIONS) la auth
   correcta es claude.ai OAuth, así que removemos el key antes y lo
   restauramos en finally. En CI mantenemos el key (sin OAuth disponible).
3. **Tool call logging + destructive tracking**: cuenta turns, tool calls,
   edit calls; detecta comandos destructivos (npm install, etc.) que
   probablemente causen tsc-fail downstream.

El resultado es un `AgentRunOutcome` value object. La excepción del SDK (si
ocurre) se captura como string en `outcome.error` — el caller decide si
re-raise o propagar via stage.

Imports del SDK son **diferidos al método run()** para que este módulo
cargue aunque `claude_agent_sdk` no esté instalado (tests, contextos donde
solo se valida la fundación).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, cast

from ..domain.agent_run import DESTRUCTIVE_COMMAND_PATTERNS, AgentRunOutcome, ToolCall
from ..log import logger


# Wall-clock cap on a single agent run. The SDK's ``max_turns`` is a LOGICAL
# guard against runaway agents — it does not protect against a stalled tool
# call where the SDK's async generator simply never yields the next message.
# When that happens (CLI hang, network stall, runaway grep on a deep tree),
# the outer ``asyncio.run`` blocks forever and the GitHub Actions step only
# dies at the workflow-level 6h timeout.
#
# 15 min is generous for a real remedy turn budget (read, edit, build, commit,
# push, submit review) on a small change. Override per-runner via constructor.
#
# Anchor: 2026-05-18 — /ai-remedy on PR #1427 hung the Claude Agent SDK step
# for 50+ min. ``max_turns`` did not fire because no message ever arrived to
# tick the counter. This timeout is the missing wall-clock guard.
_DEFAULT_WALL_CLOCK_TIMEOUT_SECONDS = 900


# Heartbeat cadence + stuck-tool detection thresholds. The wall-clock guard
# above mercifully kills a hung run, but until it fires the GitHub Actions
# job log shows only "in_progress" — no visibility into WHAT is hung.
# The heartbeat task logs the agent's live counters every N seconds so an
# operator can see where time is going as it happens, and flags single tool
# calls that have been running suspiciously long.
#
# Anchor: 2026-05-18 — when the SDK step was stuck for 50+ min, the user
# correctly inferred "stuck in a grep" but had no live signal to confirm
# it; the diagnosis came from post-hoc log inspection. With the heartbeat,
# the same situation surfaces as `[heartbeat][TOOL STALLED] current_tool=Grep`
# in the live job log within 2 minutes.
_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60
_DEFAULT_STUCK_TOOL_WARNING_SECONDS = 120


# Per-tool truncation budget for the rendered tool_flow / mermaid sequence.
# Short enough to stay inside mermaid's line-length tolerance and the
# GITHUB_STEP_SUMMARY rendering width; long enough to identify the call.
_TOOL_INPUT_SUMMARY_MAX = 80


def _summarize_tool_input(name: str, tool_input: dict[str, Any]) -> str:
    """Render-safe, truncated one-line summary of a tool's input.

    Strips characters that break mermaid (`, ", |, newline) and tightens
    long inputs to keep both the markdown table and the sequenceDiagram
    readable. The returned string is safe to embed inline without further
    escaping at the render site.
    """
    def _clean(s: str, limit: int = _TOOL_INPUT_SUMMARY_MAX) -> str:
        s = (s or "").replace("`", "´").replace('"', "'").replace("|", "/")
        s = s.replace("\n", " ").replace("\r", " ")
        if len(s) > limit:
            s = s[: limit - 1] + "…"
        return s.strip()

    if name == "Bash":
        return _clean(str(tool_input.get("command", "")), 60)
    if name in ("Read", "Edit", "Write"):
        return _clean(str(tool_input.get("file_path") or tool_input.get("path", "")))
    if name == "Grep":
        pattern = _clean(str(tool_input.get("pattern", "")), 40)
        path = _clean(str(tool_input.get("path", ".")), 30)
        return f"{pattern} in {path}"
    if name == "Glob":
        return _clean(str(tool_input.get("pattern", "")), 60)
    if name.startswith("mcp__"):
        return _clean(name[len("mcp__"):], 60)
    # Generic fallback — short repr of input
    try:
        return _clean(str(tool_input), 60)
    except Exception:
        return ""


class ClaudeSDKAgentRunner:
    """AgentRunner backed by ``claude_agent_sdk.query()``.

    Instanciable con defaults razonables del resolve pipeline (claude-sonnet-4-6,
    tools Read/Edit/Bash/Glob/Grep/Write, bypassPermissions). Customizable
    via constructor para otros usos (e.g. agente más restringido en review).
    """

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        allowed_tools: tuple[str, ...] = (
            "Read",
            "Edit",
            "Bash",
            "Glob",
            "Grep",
            "Write",
        ),
        permission_mode: str = "bypassPermissions",
        setting_sources: tuple[str, ...] = ("project",),
        destructive_patterns: tuple[str, ...] = DESTRUCTIVE_COMMAND_PATTERNS,
        wall_clock_timeout_seconds: int = _DEFAULT_WALL_CLOCK_TIMEOUT_SECONDS,
        heartbeat_interval_seconds: int = _DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        stuck_tool_warning_seconds: int = _DEFAULT_STUCK_TOOL_WARNING_SECONDS,
    ) -> None:
        self._model = model
        self._allowed_tools = list(allowed_tools)
        self._permission_mode = permission_mode
        self._setting_sources = list(setting_sources)
        self._destructive_patterns = destructive_patterns
        self._wall_clock_timeout_seconds = wall_clock_timeout_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._stuck_tool_warning_seconds = stuck_tool_warning_seconds

    def run(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        cwd: str,
        max_turns: int,
    ) -> AgentRunOutcome:
        """Invoca el SDK y devuelve un AgentRunOutcome.

        Síncrono desde el punto de vista del caller — el async internals se
        encapsulan con asyncio.run().

        Si el SDK levanta excepción, captura el error como string y devuelve
        un outcome con error != None (y otros campos en el estado parcial
        que alcanzó antes del crash). No re-raise — el caller decide.
        """
        # Imports diferidos: el SDK puede no estar instalado en algunos contextos
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            ToolUseBlock,
            query,
        )
        # Optional types — present in newer SDK versions for richer telemetry.
        # Falling back to None when missing keeps the runner forward-compatible
        # without breaking on older claude-agent-sdk installs.
        try:
            from claude_agent_sdk import (  # type: ignore[import-not-found]
                TextBlock,
            )
        except Exception:
            TextBlock = None  # type: ignore[assignment]
        try:
            from claude_agent_sdk import (  # type: ignore[import-not-found]
                UserMessage,
            )
        except Exception:
            UserMessage = None  # type: ignore[assignment]

        # SDK types use Literals for setting_sources/permission_mode — we pass
        # strings (matching historical inline behavior in run_resolve). Runtime
        # is permissive; cast quiets the type checker without changing values.
        options = ClaudeAgentOptions(
            cwd=cwd,
            setting_sources=cast(Any, self._setting_sources),
            allowed_tools=self._allowed_tools,
            permission_mode=cast(Any, self._permission_mode),
            system_prompt=system_prompt,
            model=self._model,
            max_turns=max_turns,
        )

        # Collectors mutables — la nonlocal magic vive en el async helper
        result_text = ""
        tool_calls = 0
        edit_calls = 0
        turns = 0
        destructive_calls: list[str] = []
        # Heartbeat / stuck-tool tracking — populated by _run, observed by
        # _heartbeat. monotonic seconds; None means no tool is currently
        # in-flight (i.e. the tool's result has been received).
        last_tool_name: str | None = None
        last_tool_started_at: float | None = None
        run_started_at = time.monotonic()
        # Telemetry collectors (additive, populated alongside the core counters)
        tool_breakdown: dict[str, int] = {}
        files_touched: set[str] = set()
        tool_flow: list[ToolCall] = []
        tool_failures: list[str] = []
        assistant_texts: list[str] = []
        total_cost_usd = 0.0
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0
        duration_ms = 0
        model_name = ""

        # Env var dance — drops ANTHROPIC_API_KEY so the SDK falls through
        # to CLAUDE_CODE_OAUTH_TOKEN auth (Max quota, $0 API). Two triggers:
        #
        # 1. CLAUDE_CODE_OAUTH_TOKEN set anywhere (CI or local) — explicit
        #    opt-in to OAuth billing. This is the path that closes the
        #    /ai-review and /ai-remedy billing channels. The 5-line dance
        #    that replaced the entire AIR M6.2 stack —
        #    engineering-notes/audits/air-abandoned-2026-05-11/.
        #
        # 2. Local run with ANTHROPIC_API_KEY set (legacy) — Bernard's
        #    laptop has the API key for other tools; the SDK respects
        #    claude.ai session auth when the env key is absent.
        saved_key: str | None = None
        has_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
        is_local = not os.environ.get("GITHUB_ACTIONS")
        if (has_oauth or is_local) and os.environ.get("ANTHROPIC_API_KEY"):
            saved_key = os.environ.pop("ANTHROPIC_API_KEY")
            logger.info(
                "  Removed ANTHROPIC_API_KEY (%s auth path)",
                "OAuth/Max quota" if has_oauth else "local claude.ai",
            )

        async def _run() -> None:
            nonlocal result_text, tool_calls, edit_calls, turns
            nonlocal total_cost_usd, input_tokens, output_tokens
            nonlocal cache_read_tokens, cache_creation_tokens
            nonlocal duration_ms, model_name
            nonlocal last_tool_name, last_tool_started_at
            async for message in query(prompt=user_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    turns += 1
                    for block in getattr(message, "content", []) or []:
                        if isinstance(block, ToolUseBlock):
                            tool_calls += 1
                            last_tool_name = block.name
                            last_tool_started_at = time.monotonic()
                            tool_breakdown[block.name] = (
                                tool_breakdown.get(block.name, 0) + 1
                            )
                            tool_input = dict(block.input or {})
                            tool_flow.append(
                                ToolCall(
                                    name=block.name,
                                    input_summary=_summarize_tool_input(
                                        block.name, tool_input
                                    ),
                                )
                            )
                            if block.name in ("Edit", "Write"):
                                edit_calls += 1
                                fpath = (
                                    tool_input.get("file_path")
                                    or tool_input.get("path")
                                    or ""
                                )
                                if isinstance(fpath, str) and fpath:
                                    files_touched.add(fpath)
                            cmd = ""
                            if block.name == "Bash":
                                cmd = str(tool_input.get("command", ""))
                                logger.info(f"  [tool] Bash: {cmd[:200]}")
                            else:
                                path = (
                                    tool_input.get("file_path")
                                    or tool_input.get("path")
                                    or ""
                                )
                                logger.info(f"  [tool] {block.name}: {str(path)[:200]}")
                            if cmd and any(
                                pat in cmd for pat in self._destructive_patterns
                            ):
                                destructive_calls.append(cmd[:500])
                                logger.warning(
                                    f"  [tool][DESTRUCTIVE] Agent ran a command that "
                                    f"mutates node_modules or deps: {cmd[:200]}"
                                )
                        elif TextBlock is not None and isinstance(block, TextBlock):
                            text = getattr(block, "text", "") or ""
                            if text.strip():
                                assistant_texts.append(text)
                elif UserMessage is not None and isinstance(message, UserMessage):
                    # Tool results flow back as user messages. The arrival of
                    # any UserMessage means the previous tool call has
                    # returned — clear the in-flight marker so the heartbeat
                    # stops reporting it as "current_tool".
                    last_tool_name = None
                    last_tool_started_at = None
                    # We harvest the ones flagged is_error=True for the ❌ Tool failures
                    # collapsible. Content shape: list of blocks, each with
                    # type=tool_result, is_error, content (string or list).
                    for block in getattr(message, "content", []) or []:
                        if getattr(block, "is_error", False):
                            content = getattr(block, "content", "") or ""
                            if isinstance(content, list):
                                content = " ".join(
                                    str(getattr(c, "text", c))[:200] for c in content
                                )
                            content_s = str(content)[:300].replace("\n", " ")
                            tool_failures.append(content_s)
                elif isinstance(message, ResultMessage):
                    result_text = getattr(message, "result", "") or ""
                    # Defensive getattr — SDK field naming has shifted over
                    # versions. We try the canonical names first and fall
                    # back to zero so an older SDK doesn't crash this.
                    total_cost_usd = float(
                        getattr(message, "total_cost_usd", 0.0) or 0.0
                    )
                    duration_ms = int(
                        getattr(message, "duration_ms", 0)
                        or getattr(message, "duration_api_ms", 0)
                        or 0
                    )
                    model_name = str(
                        getattr(message, "model", "")
                        or getattr(message, "model_name", "")
                        or ""
                    )
                    usage_obj = getattr(message, "usage", None)
                    if usage_obj is not None:
                        # usage may be dict-like or attribute-bearing
                        def _u(key: str) -> int:
                            if isinstance(usage_obj, dict):
                                return int(usage_obj.get(key, 0) or 0)
                            return int(getattr(usage_obj, key, 0) or 0)
                        input_tokens = _u("input_tokens")
                        output_tokens = _u("output_tokens")
                        cache_read_tokens = _u("cache_read_input_tokens")
                        cache_creation_tokens = _u("cache_creation_input_tokens")

        error: str | None = None
        timeout_s = self._wall_clock_timeout_seconds
        heartbeat_interval = self._heartbeat_interval_seconds
        stuck_tool_threshold = self._stuck_tool_warning_seconds

        async def _heartbeat() -> None:
            # Periodic liveness log so an operator watching the CI job can see
            # WHERE the agent is spending time, not just that it is alive.
            # Reads the same nonlocal counters _run is mutating; no locking
            # needed because Python asyncio is single-threaded — the heartbeat
            # task only runs at await points in _run, so each read sees a
            # consistent snapshot.
            try:
                while True:
                    await asyncio.sleep(heartbeat_interval)
                    elapsed = int(time.monotonic() - run_started_at)
                    base = (
                        f"[heartbeat] elapsed={elapsed}s turns={turns} "
                        f"tool_calls={tool_calls} edit_calls={edit_calls}"
                    )
                    if last_tool_name and last_tool_started_at is not None:
                        tool_dur = int(time.monotonic() - last_tool_started_at)
                        msg = f"{base} current_tool={last_tool_name} for={tool_dur}s"
                        if tool_dur >= stuck_tool_threshold:
                            logger.warning(f"  {msg} (TOOL STALLED)")
                            continue
                        logger.info(f"  {msg}")
                    else:
                        logger.info(f"  {base}")
            except asyncio.CancelledError:
                # Normal shutdown path when _run finishes or the wall-clock
                # timeout fires. Suppress the cancellation so it does not
                # surface as an error in the outer try/except.
                return

        async def _run_with_timeout() -> None:
            # asyncio.wait_for cancels the inner task on expiry. The nonlocal
            # state (turns/tool_calls/etc.) collected before the timeout is
            # preserved on the outer closure, so the returned outcome still
            # reflects how far the agent got.
            heartbeat_task = asyncio.create_task(_heartbeat())
            try:
                await asyncio.wait_for(_run(), timeout=timeout_s)
            finally:
                heartbeat_task.cancel()
                # Wait briefly for the task to actually finish so we do not
                # leak a pending task into the next asyncio.run if this method
                # is called twice on the same runner instance.
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass

        try:
            asyncio.run(_run_with_timeout())
        except (asyncio.TimeoutError, TimeoutError):
            error = (
                f"Agent run exceeded wall-clock timeout of {timeout_s}s. "
                f"State at timeout: turns={turns}, tool_calls={tool_calls}, "
                f"edit_calls={edit_calls}. The SDK query loop did not yield "
                f"the next message in time — likely a stalled tool call or "
                f"a hung CLI subprocess."
            )
            logger.error(f"  Agent timeout: {error}")
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.error(f"  Agent failed: {e}")
        finally:
            if saved_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved_key

        # Detect SDK-level errors smuggled in as result_text. The Agent SDK
        # sometimes returns short error strings (rate limit, credit balance,
        # auth) as a "success" ResultMessage instead of raising. Without
        # this check the result_text propagates downstream and consumers
        # (e.g. claude_review_synthesize) try to parse the error string as
        # their expected output, silently producing empty payloads — exactly
        # what burned the first adversarial production run on PR #1364.
        if error is None and result_text:
            sdk_error = _detect_sdk_error_text(result_text)
            if sdk_error:
                error = f"SDK returned error text instead of result: {sdk_error}"
                logger.error(f"  Agent SDK returned error in result_text: {sdk_error}")
                # Clear result_text so downstream consumers don't treat the
                # error string as a partial answer.
                result_text = ""

        # Sort tool_breakdown deterministically by count desc, then name asc
        # so the rendered output is stable across runs.
        sorted_breakdown = tuple(
            sorted(tool_breakdown.items(), key=lambda kv: (-kv[1], kv[0]))
        )

        return AgentRunOutcome(
            result_text=result_text,
            turns=turns,
            tool_calls=tool_calls,
            edit_calls=edit_calls,
            destructive_calls=tuple(destructive_calls),
            error=error,
            total_cost_usd=total_cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            duration_ms=duration_ms,
            model_name=model_name,
            tool_breakdown=sorted_breakdown,
            files_touched=tuple(sorted(files_touched)),
            tool_flow=tuple(tool_flow),
            tool_failures=tuple(tool_failures),
            assistant_texts=tuple(assistant_texts),
        )


# Heuristic detection of SDK error strings smuggled through as result_text.
# These are short, lowercase-matched substrings that appear in the SDK's
# error response payloads but never in legitimate model output for a
# review/synthesis prompt.
_SDK_ERROR_MARKERS: tuple[str, ...] = (
    "credit balance is too low",
    "credit balance too low",
    "rate limit exceeded",
    "invalid api key",
    "authentication failed",
    "anthropic api error",
    "insufficient quota",
)


def _detect_sdk_error_text(text: str) -> str | None:
    """Return the marker if ``text`` is an SDK error string, else None.

    Heuristic: SDK error responses are short (~40 chars), have no JSON,
    no markdown structure, and contain one of the known marker substrings.
    Long outputs that happen to mention "rate limit" inside a code review
    finding are NOT caught — the length gate filters them out.
    """
    if len(text) > 200:
        # Real model output for a review/synthesis prompt is always longer
        # than this. SDK errors are short.
        return None
    lowered = text.strip().lower()
    for marker in _SDK_ERROR_MARKERS:
        if marker in lowered:
            return marker
    return None

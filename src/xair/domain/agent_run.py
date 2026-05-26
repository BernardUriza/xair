"""AgentRunOutcome — resultado de invocar un agente LLM con tool-use.

Value object inmutable que captura lo que pasó cuando un agente (Claude
Agent SDK, Codex CLI, etc.) corrió contra un prompt: cuántos turns, tool
calls, edit calls, y qué comandos destructivos disparó.

Vive en `domain/` porque es estructura de datos del dominio — el contrato
de lo que un agent_run produce, independiente de qué proveedor concreto
lo generó.

Los stages que consumen un agent_run (substance_gate, verify_build, etc.)
tipan contra este shape, no contra el AssistantMessage/ResultMessage del
SDK.
"""

from __future__ import annotations

from dataclasses import dataclass


# Comandos que indican que el agente está mutando el environment de forma
# que probablemente rompa un tsc/build posterior. La presencia de cualquiera
# en `destructive_calls` señaliza que un tsc-fail downstream es likely
# self-inflicted del agente, no environment.
#
# Extraído como constante para que `infra/agent_runner.py` y el monolito
# `pipelines/resolve.py` compartan la misma lista. Si la lista crece, se
# actualiza acá una vez.
DESTRUCTIVE_COMMAND_PATTERNS: tuple[str, ...] = (
    "npm install",
    "npm ci",
    "npm uninstall",
    "npm update",
    "yarn add",
    "yarn remove",
    "yarn install",
    "pnpm install",
    "pnpm add",
    "pnpm remove",
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One entry in the agent's tool-call timeline.

    Captures enough to render a ludic per-call markdown row or mermaid
    sequenceDiagram message: tool name + a short, truncated, render-safe
    summary of the input (the file path for Read/Edit/Write, the command
    for Bash, the pattern for Grep/Glob, etc.).

    ``input_summary`` is already truncated and stripped of newlines/pipes
    so it can be embedded in mermaid / markdown tables without escaping
    at render time.
    """

    name: str
    input_summary: str = ""


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    """Resultado de un agent run — value object inmutable.

    Core attributes (callers since the dataclass was introduced):
        result_text: la respuesta final del agente (ResultMessage.result).
            Vacío si el agente crasheó antes de emitir resultado.
        turns: cuántas AssistantMessages produjo (proxy de "turns" del agente)
        tool_calls: total de ToolUseBlocks invocados
        edit_calls: subset que son Edit o Write — feed del substance_gate
        destructive_calls: comandos que matchean DESTRUCTIVE_COMMAND_PATTERNS.
            Tuple para inmutabilidad; cada elemento es el cmd truncado a 500 chars.
        error: si el agente crasheó, el string de la excepción. None si OK.

    Telemetry attributes (added 2026-05-13 for the ludic Job Summary
    renderer — backwards-compatible, default to zero/empty so existing
    callers that only read the core attrs keep working):
        total_cost_usd: cost reported by the SDK's ResultMessage.
        input_tokens / output_tokens / cache_read_tokens / cache_creation_tokens:
            usage totals from ResultMessage.usage.
        duration_ms: total wall-clock from the SDK.
        model_name: which model the SDK reports having used.
        tool_breakdown: per-tool-name counts. Sums to ``tool_calls``.
        files_touched: deduped file paths from Edit + Write blocks.
        tool_flow: ordered list of ToolCall — the timeline. Same length as
            ``tool_calls``.
        tool_failures: tool_result messages where ``is_error=True``.
            Captured from the user-side messages the SDK yields back
            between turns. Each entry is a short, truncated error string.
        assistant_texts: text blocks the agent emitted (its "reasoning" /
            narration, separate from tool_use blocks). Useful for the
            collapsible 🧠 Agent reasoning section.
    """

    result_text: str = ""
    turns: int = 0
    tool_calls: int = 0
    edit_calls: int = 0
    destructive_calls: tuple[str, ...] = ()
    error: str | None = None

    # Telemetry (additive, defaults preserve old behavior)
    total_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    duration_ms: int = 0
    model_name: str = ""
    tool_breakdown: tuple[tuple[str, int], ...] = ()
    files_touched: tuple[str, ...] = ()
    tool_flow: tuple[ToolCall, ...] = ()
    tool_failures: tuple[str, ...] = ()
    assistant_texts: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        """True si el agente terminó sin crash."""
        return self.error is None

    @property
    def has_substance(self) -> bool:
        """True si el agente al menos hizo un Edit/Write — heurística mínima
        de "el agente trabajó". Lecutura más estricta queda en substance_gate
        policy (cuenta archivos cambiados via git diff)."""
        return self.edit_calls > 0

"""Shared ludic markdown renderer for AgentRunOutcome.

Renders an AgentRunOutcome as a rich markdown string for $GITHUB_STEP_SUMMARY:

- Header with PR link + final review state
- 📊 Run telemetry table (cost, duration, turns, model, errored)
- 🎟️ Token usage table (input, output, cache read, cache creation)
- 🛠️ Agent activity bullet (total + per-tool breakdown with emojis)
- 📁 Files touched (bulleted list)
- 🎬 Tool flow as mermaid sequenceDiagram (first 25 calls)
- 📜 Full execution timeline as collapsible numbered table
- 🧠 Agent reasoning as collapsible blockquotes
- ❌ Tool failures as collapsible bullet list
- 💬 Review body / 🤖 Agent final message

This module is the single source of truth for "what a XAIR agent run
looks like" in any GitHub Actions Job Summary. ``pipelines/remedy.py``
calls it; when ``pipelines/resolve.py`` and ``claude_review_synthesize``
migrate to use it, the same look-and-feel propagates for free.

The renderer ONLY reads from the dataclass fields. It does NO IO and
NO API calls — pass any extra context (PR URL, repo, review state) in
as plain arguments. The caller is responsible for writing the output
string to ``$GITHUB_STEP_SUMMARY`` or wherever.
"""

from __future__ import annotations

from typing import Optional

from ..domain.agent_run import AgentRunOutcome, ToolCall


# Per-tool emoji map — keep in sync with the bash version that lived in
# ai-dispatch.yml so reviewers see the same icons across the two paths
# while the migration is in flight.
_TOOL_EMOJI: dict[str, str] = {
    "Bash": "🐚",
    "Read": "📖",
    "Edit": "✏️",
    "Write": "📝",
    "Grep": "🔍",
    "Glob": "🌐",
}


def _tool_icon(name: str) -> str:
    """Return the emoji + tool name pair used as the row prefix.

    MCP tools all share 🌍 with the ``mcp__`` prefix stripped from the
    displayed name. Unknown tools get the generic 🔧 wrench.
    """
    if name in _TOOL_EMOJI:
        return f"{_TOOL_EMOJI[name]} {name}"
    if name.startswith("mcp__"):
        return f"🌍 {name[len('mcp__'):]}"
    return f"🔧 {name}"


def _humanize_duration(duration_ms: int) -> str:
    """``153000`` → ``2m 33s``. Falls back to ``Ns`` under a minute."""
    if duration_ms <= 0:
        return "0s"
    seconds = duration_ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    return f"{minutes}m {rem}s"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def render_agent_summary(
    outcome: AgentRunOutcome,
    *,
    kind: str,
    repo: str = "",
    pr_number: Optional[int] = None,
    review_state: str = "",
    extra_header_lines: tuple[str, ...] = (),
    body_override: str = "",
    timeline_cap: int = 200,
    mermaid_cap: int = 25,
) -> str:
    """Render the outcome into a markdown blob ready to append to a summary.

    Args:
        outcome: the agent run result.
        kind: descriptive label for the run (``"remedy"``, ``"review"``,
            ``"work"``). Becomes part of the title.
        repo: ``owner/name`` of the target repo, for the PR link. Optional.
        pr_number: PR number for the link. Optional.
        review_state: the final review state if the agent posted one
            (``"APPROVED"``, ``"COMMENTED"``, ``"REQUEST_CHANGES"``, etc.).
            Pass ``""`` if unknown — the field renders as ``(none)``.
        extra_header_lines: additional ``**Key:** value`` bullets to insert
            in the header block. Each item is rendered on its own line.
        body_override: if set, replaces the default "Review body /
            Agent final message" trailer. Useful when the caller has
            scraped the actual posted review from GitHub.
        timeline_cap: max rows in the 📜 collapsible execution table.
        mermaid_cap: max messages in the 🎬 mermaid sequenceDiagram.
    """
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────
    lines.append(f"## 🛠️ XAIR {kind} run")
    lines.append("")
    if pr_number is not None and repo:
        lines.append(
            f"**PR:** [#{pr_number}](https://github.com/{repo}/pull/{pr_number})"
        )
    if repo:
        lines.append(f"**Repo:** `{repo}`")
    if review_state:
        lines.append(f"**Final review state:** `{review_state}`")
    else:
        lines.append("**Final review state:** `(none)`")
    for extra in extra_header_lines:
        lines.append(extra)
    lines.append("")

    # ── 📊 Run telemetry ──────────────────────────────────────
    lines.append("### 📊 Run telemetry")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    cost_str = (
        f"${outcome.total_cost_usd:.4f}" if outcome.total_cost_usd > 0 else "(n/a)"
    )
    lines.append(f"| 💰 Cost | {cost_str} |")
    lines.append(f"| ⏱️ Duration | {_humanize_duration(outcome.duration_ms)} |")
    lines.append(f"| 🔄 Turns | {outcome.turns} |")
    lines.append(f"| 🧠 Model | `{outcome.model_name or '(unknown)'}` |")
    lines.append(f"| ⚠️ Errored | `{not outcome.succeeded}` |")
    lines.append("")

    # ── 🎟️ Token usage ────────────────────────────────────────
    if (
        outcome.input_tokens
        or outcome.output_tokens
        or outcome.cache_read_tokens
        or outcome.cache_creation_tokens
    ):
        lines.append("### 🎟️ Token usage")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| ↘️ Input | {outcome.input_tokens:,} |")
        lines.append(f"| ↗️ Output | {outcome.output_tokens:,} |")
        lines.append(f"| 💾 Cache read | {outcome.cache_read_tokens:,} |")
        lines.append(f"| 🆕 Cache creation | {outcome.cache_creation_tokens:,} |")
        lines.append("")

    # ── 🛠️ Agent activity ────────────────────────────────────
    lines.append("### 🛠️ Agent activity")
    lines.append("")
    breakdown_parts: list[str] = []
    for name, count in outcome.tool_breakdown:
        breakdown_parts.append(f"{_tool_icon(name)}: {count}")
    fail_suffix = (
        f" ({len(outcome.tool_failures)} failed)" if outcome.tool_failures else ""
    )
    lines.append(f"- **Tool calls total:** {outcome.tool_calls}{fail_suffix}")
    if breakdown_parts:
        lines.append("  - " + " · ".join(breakdown_parts))

    if outcome.files_touched:
        lines.append("")
        lines.append("**📁 Files touched (Edit + Write):**")
        for f in outcome.files_touched[:20]:
            lines.append(f"- `{f}`")
        if len(outcome.files_touched) > 20:
            lines.append(f"- _…and {len(outcome.files_touched) - 20} more_")

    if outcome.destructive_calls:
        lines.append("")
        lines.append(f"**⚠️ Destructive commands ({len(outcome.destructive_calls)}):**")
        for cmd in outcome.destructive_calls[:10]:
            lines.append(f"- `{_truncate(cmd.replace('`', '´'), 120)}`")

    lines.append("")

    # ── 🎬 Tool flow (mermaid sequenceDiagram) ────────────────
    if outcome.tool_flow:
        lines.append(f"### 🎬 Tool flow (first {mermaid_cap} calls)")
        lines.append("")
        lines.append("```mermaid")
        lines.append("sequenceDiagram")
        lines.append("  participant A as 🤖 Agent")
        lines.append("  participant W as 🛠️ Workspace")
        for call in outcome.tool_flow[:mermaid_cap]:
            lines.append(f"  A->>W: {_mermaid_label(call)}")
        lines.append("```")
        lines.append("")

    # ── 📜 Full execution timeline (collapsible table) ────────
    if outcome.tool_flow:
        lines.append(
            f"<details><summary>📜 Full execution timeline ({outcome.tool_calls} tool calls)</summary>"
        )
        lines.append("")
        lines.append("| # | Tool | Target |")
        lines.append("|---|---|---|")
        for idx, call in enumerate(outcome.tool_flow[:timeline_cap], start=1):
            target = f"`{call.input_summary}`" if call.input_summary else ""
            lines.append(f"| {idx} | {_tool_icon(call.name)} | {target} |")
        if len(outcome.tool_flow) > timeline_cap:
            lines.append(
                f"| … | _truncated_ | _{len(outcome.tool_flow) - timeline_cap} more calls_ |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ── 🧠 Agent reasoning (collapsible blockquotes) ──────────
    if outcome.assistant_texts:
        lines.append(
            f"<details><summary>🧠 Agent reasoning ({len(outcome.assistant_texts)} text blocks)</summary>"
        )
        lines.append("")
        for text in outcome.assistant_texts[:50]:
            # blockquote each line of the text
            for line in text.splitlines():
                if line.strip():
                    lines.append(f"> {line}")
            lines.append("")
        if len(outcome.assistant_texts) > 50:
            lines.append(f"_…and {len(outcome.assistant_texts) - 50} more text blocks._")
            lines.append("")
        lines.append("</details>")
        lines.append("")

    # ── ❌ Tool failures (collapsible) ────────────────────────
    if outcome.tool_failures:
        lines.append(
            f"<details><summary>❌ Tool failures ({len(outcome.tool_failures)})</summary>"
        )
        lines.append("")
        for fail in outcome.tool_failures[:50]:
            safe = fail.replace("`", "´")
            lines.append(f"- `{_truncate(safe, 200)}`")
        if len(outcome.tool_failures) > 50:
            lines.append(f"- _…and {len(outcome.tool_failures) - 50} more failures_")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ── Error block (only when the agent crashed) ─────────────
    if outcome.error:
        lines.append("### ❌ Agent error")
        lines.append("")
        lines.append("```")
        lines.append(_truncate(outcome.error, 1500))
        lines.append("```")
        lines.append("")

    # ── Footer: review body / agent final message ─────────────
    lines.append("---")
    lines.append("")
    if body_override:
        lines.append("### 💬 Review body")
        lines.append("")
        lines.append(body_override)
    elif outcome.result_text:
        lines.append("### 🤖 Agent final message")
        lines.append("")
        lines.append(_truncate(outcome.result_text, 5000))
    else:
        lines.append(
            "_The agent did not post a top-level review body and produced no final text. "
            "Check the step log above._"
        )

    return "\n".join(lines) + "\n"


def _mermaid_label(call: ToolCall) -> str:
    """Build a mermaid-safe message label for one tool call.

    The ``input_summary`` is already cleaned by ``_summarize_tool_input``
    in the runner, so this function only needs to prepend the emoji.
    """
    if call.name in _TOOL_EMOJI:
        emoji = _TOOL_EMOJI[call.name]
    elif call.name.startswith("mcp__"):
        emoji = "🌍"
    else:
        emoji = "🔧"
    if call.input_summary:
        return f"{emoji} {call.input_summary}"
    return f"{emoji} {call.name}"

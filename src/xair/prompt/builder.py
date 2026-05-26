"""Prompt assembly -- tiered authority hierarchy with budget-aware rendering.

Tiers are injected into the user message in priority order.
When deep analysis is present, lower-priority tiers are dropped
to stay within token budget.

Budget model:
- TOTAL_BUDGET: model-specific context window limit (conservative)
- System prompt + invariants: counted first (fixed cost)
- Diff: counted second (variable, already truncated by gatherer)
- Tiers: fill the remaining budget in priority order
- Safety margin: 10% reserved for prompt template overhead
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from ..domain.invariants import INVARIANTS
from ..domain.models import ReviewContext
from ..infra.constants import OPENAI_MODEL, TEMPLATES_DIR
from ..log import logger

# -- Budget constants ---------------------------------------------------

_CHARS_PER_TOKEN = 4  # rough approximation (conservative for English + code)

# Model context windows (input tokens, conservative estimates)
_MODEL_BUDGETS: dict[str, int] = {
    OPENAI_MODEL: 128_000,
    # Legacy entries retained for backward compatibility with existing logs/configs.
    "gpt-5.4": 128_000,
    "gpt-4.1": 128_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
}
_DEFAULT_BUDGET_TOKENS = 128_000
_SAFETY_MARGIN = 0.10  # reserve 10% for template overhead and output tokens
_MAX_TIER_TOKENS = 30_000  # max total tier budget (backward compat)


# ── Template loading (cached, explicit errors) ───────────────────


@lru_cache(maxsize=32)
def _load(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_tier(name: str) -> str:
    return _load(TEMPLATES_DIR / "tiers" / f"{name}.md")


# ── Tier definitions ─────────────────────────────────────────────

# Priority order (highest first). Priority determines drop order when over budget.
# droppable=False means the tier is never sacrificed.
_TIERS: list[dict] = [
    {"name": "learnings",      "accessor": lambda ctx: ctx.learnings,      "droppable": False},
    {"name": "rules",          "accessor": lambda ctx: ctx.rules,          "droppable": False},
    {"name": "ci_status",      "accessor": lambda ctx: ctx.ci_status,      "droppable": False},
    {"name": "deep_analysis",  "accessor": lambda ctx: ctx.deep_analysis,  "droppable": False},
    {"name": "prior_art",      "accessor": lambda ctx: ctx.prior_art,      "droppable": True},
    {"name": "css_health",     "accessor": lambda ctx: ctx.css_health,     "droppable": True},
    {"name": "full_files",     "accessor": lambda ctx: ctx.full_files,     "droppable": True},
]


def _render_tier(name: str, content: str) -> str:
    content_stripped = content.strip()
    if not content_stripped:
        logger.debug(f"  [builder] _render_tier({name}): SKIPPED — content is empty")
        return ""
    template = _load_tier(name)
    rendered = template.format(content=content) + "\n"
    logger.debug(f"  [builder] _render_tier({name}): content={len(content)} chars, rendered={len(rendered)} chars")
    return rendered


def _render_all_tiers(ctx: ReviewContext) -> dict[str, str]:
    """Render tiers with budget-aware dropping.

    When deep_analysis is present and the total tier content exceeds
    the token budget, droppable tiers are removed from lowest priority
    (full_files first, then css_health, then prior_art).
    """
    has_deep = bool(ctx.deep_analysis and ctx.deep_analysis.strip())

    # Phase 1: render all tiers
    rendered: list[tuple[str, str, bool]] = []  # (name, rendered_text, droppable)
    for tier in _TIERS:
        content = tier["accessor"](ctx)
        text = _render_tier(tier["name"], content)
        rendered.append((tier["name"], text, tier["droppable"]))

    # Phase 2: when deep analysis is present, drop full_files unconditionally
    # (Claude already read those files via Agent SDK tools) then check budget
    if has_deep:
        for i, (name, text, droppable) in enumerate(rendered):
            if name == "full_files" and text:
                dropped_chars = len(text)
                rendered[i] = (name, "", True)
                logger.debug(f"  [builder] DROPPED full_files ({dropped_chars} chars / {dropped_chars // _CHARS_PER_TOKEN}~tokens) -- redundant when deep analysis active")

        total_chars = sum(len(text) for _, text, _ in rendered)
        budget_chars = _MAX_TIER_TOKENS * _CHARS_PER_TOKEN
        logger.debug(f"  [builder] budget check: total={total_chars} chars, budget={budget_chars} chars, over={total_chars > budget_chars}")

        if total_chars > budget_chars:
            for i in range(len(rendered) - 1, -1, -1):
                name, text, droppable = rendered[i]
                if not droppable or not text:
                    continue

                dropped_chars = len(text)
                rendered[i] = (name, "", True)
                total_chars -= dropped_chars
                logger.debug(f"  [builder] DROPPED {name} ({dropped_chars} chars / {dropped_chars // _CHARS_PER_TOKEN}~tokens)")

                if total_chars <= budget_chars:
                    break

    # Phase 3: build the template placeholder dict — log every tier
    logger.debug(f"  [builder] === FINAL TIER MAP ===")
    result: dict[str, str] = {}
    for name, text, _ in rendered:
        key = f"tier_{name}"
        result[key] = text
        logger.debug(f"  [builder]   {key}: {len(text)} chars {'OK' if text else '(empty)'}")

    # resolved_threads has no template — raw text
    result["tier_resolved"] = (
        ctx.resolved_threads + "\n\n" if ctx.resolved_threads.strip() else ""
    )
    return result


# ── System prompt assembly ───────────────────────────────────────


def _build_system_prompt(prompt_file: str) -> str:
    variant_path = Path(prompt_file)
    base_path = variant_path.parent / "base.md"
    return _load(base_path) + "\n" + _load(variant_path)


def _diff_header(ctx: ReviewContext) -> str:
    if ctx.truncated:
        return f"[PR DIFF -- truncated to {ctx.max_diff_bytes} bytes, some files omitted]"
    return "[PR DIFF]"


def _prompt_hash(system: str, user_prefix: str) -> str:
    return hashlib.sha256((system + user_prefix).encode("utf-8")).hexdigest()[:12]


# ── Public API ───────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Approximate token count from character length."""
    return len(text) // _CHARS_PER_TOKEN


def _check_total_budget(
    system_prompt: str,
    user_message: str,
    model: str,
    max_output_tokens: int = 8192,
) -> None:
    """Log a warning if the assembled prompt likely exceeds the model's context window.

    This is a safety net, not a hard gate -- the LLM provider will reject
    with a clear API error if we actually exceed the limit.
    """
    budget = _MODEL_BUDGETS.get(model, _DEFAULT_BUDGET_TOKENS)
    usable = int(budget * (1 - _SAFETY_MARGIN)) - max_output_tokens

    system_tokens = _estimate_tokens(system_prompt)
    user_tokens = _estimate_tokens(user_message)
    total = system_tokens + user_tokens

    if total > usable:
        overage = total - usable
        logger.warning(
            f"::warning::Prompt budget exceeded: ~{total} tokens "
            f"(system: ~{system_tokens}, user: ~{user_tokens}) "
            f"vs ~{usable} usable for model {model}. "
            f"Over by ~{overage} tokens. LLM call may fail."
        )
    else:
        headroom = usable - total
        logger.debug(
            f"Prompt budget: ~{total} tokens "
            f"(system: ~{system_tokens}, user: ~{user_tokens}), "
            f"~{headroom} headroom for {model}"
        )


def _compact_diff(diff: str) -> str:
    """Extract only file paths and hunk headers from a unified diff.

    When deep analysis is active, Claude already inspected the full code
    via Read/Glob/Grep. GPT only needs the structural skeleton (which
    files changed, which functions/areas) to place inline comments.

    Handles both standard unified diffs (diff --git headers) and the
    per-file patch fallback (no git headers, just --- / +++ / @@).
    """
    lines = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            lines.append(line)
        elif line.startswith("--- ") or line.startswith("+++ "):
            lines.append(line)
        elif line.startswith("@@ "):
            lines.append(line)
    if not lines:
        # Fallback: diff had no recognizable headers (raw patch text).
        # Return the full diff — compaction can't help here.
        logger.debug("  [builder] _compact_diff: no headers found, returning full diff")
        return diff
    header = "[PR DIFF -- compact: deep analysis already inspected full code]\n"
    return header + "\n".join(lines)


def build_prompt(ctx: ReviewContext) -> ReviewContext:
    """Assemble system + user messages. Returns a new frozen ReviewContext.

    Runs a budget check after assembly and logs a warning if the prompt
    is likely to exceed the model's context window.
    """
    system_prompt = _build_system_prompt(ctx.prompt_file)

    tier_blocks = _render_all_tiers(ctx)
    user_prefix = _load(TEMPLATES_DIR / "user_message.md").format(
        invariants="\n".join(INVARIANTS),
        **tier_blocks,
        diff_header=_diff_header(ctx),
    )

    has_deep = bool(ctx.deep_analysis and ctx.deep_analysis.strip())
    if has_deep:
        compact = _compact_diff(ctx.diff)
        user_message = user_prefix + "\n" + compact
        logger.debug(f"  [builder] diff compacted: {len(ctx.diff)} -> {len(compact)} chars (deep active)")
    else:
        user_message = user_prefix + "\n" + ctx.diff

    _check_total_budget(system_prompt, user_message, ctx.model)

    return replace(
        ctx,
        system_prompt=system_prompt,
        user_message=user_message,
        prompt_hash=_prompt_hash(system_prompt, user_prefix),
    )

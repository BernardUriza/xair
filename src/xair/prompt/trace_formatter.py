"""Write full AI review trace to Job Summary."""

from __future__ import annotations

import json

from .. import __version__
from ..infra.constants import TEMPLATES_DIR
from ..domain.models import ReviewContext
from ..contracts import ActionsIO
from ..log import logger

_TEMPLATE = (TEMPLATES_DIR / "trace.md").read_text(encoding="utf-8")

_RULES_WARNING = (
    "> ⚠️ **RULES NOT LOADED** — reviewer ran without security/multi-tenancy/data-privacy "
    "rules. Findings may miss critical issues. Check PROMPT_REPO_TOKEN access to "
    "xair-org/engineering-notes."
)


def _truncate_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[:max_lines])


def write_trace(ctx: ReviewContext, actions: ActionsIO, run_id: str = "") -> None:  # noqa: ARG001
    """Build the trace markdown and write it to $GITHUB_STEP_SUMMARY."""

    diff_bytes = len(ctx.diff.encode("utf-8"))
    prompt_bytes = len(ctx.user_message.encode("utf-8"))

    # Knowledge list
    knowledge_items = ctx.review.knowledge
    if knowledge_items:
        knowledge_list = "\n".join(
            f"- **[{k.type}]** {k.observation} _(confidence: {k.confidence})_"
            for k in knowledge_items
        )
    else:
        knowledge_list = "_No new observations_"

    # Review JSON subset
    review_subset = {
        "summary": ctx.review.summary,
        "findings": [
            {"severity": f.severity, "file": f.file, "line": f.line, "comment": f.comment}
            for f in ctx.review.findings
        ],
        "highlights": [
            {"file": h.file, "line": h.line, "comment": h.comment}
            for h in ctx.review.highlights
        ],
    }

    # Full response JSON
    full_response = {
        "title": ctx.review.title,
        "summary": ctx.review.summary,
        "findings": [
            {"severity": f.severity, "file": f.file, "line": f.line, "comment": f.comment}
            for f in ctx.review.findings
        ],
        "highlights": [
            {"file": h.file, "line": h.line, "comment": h.comment}
            for h in ctx.review.highlights
        ],
        "knowledge": [
            {"type": k.type, "observation": k.observation, "confidence": k.confidence}
            for k in ctx.review.knowledge
        ],
    }

    # Conditional / truncated sections
    learnings = ctx.learnings or "(none)"
    threads = ctx.resolved_threads or "(none)"
    rules_bytes = len(ctx.rules.encode("utf-8")) if ctx.rules else 0

    # Deep analysis status — the most important observability signal
    deep_in_prompt = "[DEEP ANALYSIS" in ctx.user_message
    if ctx.deep_analysis and deep_in_prompt:
        deep_status = "✅ **ON** — Claude Agent SDK"
        deep_detail = f"{len(ctx.deep_analysis)} chars in prompt"
    elif ctx.deep_analysis and not deep_in_prompt:
        deep_status = "⚠️ **GATHERED BUT NOT IN PROMPT**"
        deep_detail = f"{len(ctx.deep_analysis)} chars gathered, template missing placeholder"
    else:
        import os
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
            deep_status = "⬚ OFF — auto-heuristic skipped"
            deep_detail = "Auth present but deep not triggered"
        else:
            deep_status = "❌ **OFF — NO CLAUDE AUTH**"
            deep_detail = "Neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY set; Agent SDK disabled, GPT-only mode"

    # Prior art / CSS health status
    prior_art_status = f"{len(ctx.prior_art)} chars" if ctx.prior_art else "none"
    css_health_status = f"{len(ctx.css_health)} chars" if ctx.css_health else "none"
    ci_status_status = f"{len(ctx.ci_status)} chars" if ctx.ci_status else "none"
    selected_rules_count = len(ctx.selected_rules.splitlines()) if ctx.selected_rules else 0

    trace = _TEMPLATE.format(
        xair_version=__version__,
        pr_number=ctx.pr.number,
        pr_url=f"https://github.com/{ctx.pr.repo}/pull/{ctx.pr.number}",
        model=ctx.model,
        diff_bytes=diff_bytes,
        prompt_bytes=prompt_bytes,
        findings_count=len(ctx.review.findings),
        knowledge_count=len(knowledge_items),
        truncated=ctx.truncated,
        rules_warning=_RULES_WARNING + "\n" if rules_bytes < 1000 else "",
        deep_status=deep_status,
        deep_detail=deep_detail,
        rules_bytes=rules_bytes,
        selected_rules_count=selected_rules_count,
        prior_art_status=prior_art_status,
        css_health_status=css_health_status,
        ci_status_status=ci_status_status,
        knowledge_list=knowledge_list,
        review_json=json.dumps(review_subset, indent=2)[:3000],
        prior_art=_truncate_lines(ctx.prior_art or "(none)", 30),
        learnings_count=sum(1 for line in learnings.splitlines() if line.startswith("[")),
        learnings=_truncate_lines(learnings, 40),
        selected_rules=ctx.selected_rules or "(none)",
        resolved_count=sum(1 for line in threads.splitlines() if line.startswith("-")),
        resolved_threads=_truncate_lines(threads, 20),
        user_message=_truncate_lines(ctx.user_message, 600),
        full_response=json.dumps(full_response, indent=2),
    )

    actions.write_summary(trace)

    # Mirror key sections to stdout (job logs) so they're readable via gh API
    logger.info("=" * 60)
    logger.info("TRACE (mirrored to stdout — same as Job Summary)")
    logger.info("=" * 60)
    logger.info(f"PR: #{ctx.pr.number} | Model: {ctx.model}")
    logger.info(f"Diff: {diff_bytes} bytes | Prompt: {prompt_bytes} bytes")
    logger.info(f"Findings: {len(ctx.review.findings)} | Knowledge: {len(knowledge_items)}")
    logger.info(f"Rules: {rules_bytes} bytes | Truncated: {ctx.truncated}")
    if rules_bytes < 1000:
        logger.info("WARNING: rules < 1000 bytes — reviewer ran without rules")
    logger.info("-" * 60)
    logger.info("REVIEW OUTPUT:")
    logger.info(json.dumps(review_subset, indent=2)[:2000])
    logger.info("-" * 60)
    logger.info(f"RULES IN PROMPT ({rules_bytes} bytes):")
    rules_preview = ctx.rules[:500] if ctx.rules else "(none)"
    logger.info(rules_preview)
    if ctx.rules and len(ctx.rules) > 500:
        logger.info(f"  ... ({rules_bytes - 500} more bytes)")
    logger.info("-" * 60)
    check_a = "[DEEP ANALYSIS]" in ctx.user_message
    check_b = "[DEEP ANALYSIS —" in ctx.user_message
    check_c = "deep analysis" in ctx.user_message.lower()[:500]
    check_d = "deep analysis" in ctx.user_message.lower()
    deep_in_prompt = check_b or check_d
    logger.info(f"DEEP ANALYSIS IN PROMPT: {deep_in_prompt}")
    logger.debug(f"  [trace] check '[DEEP ANALYSIS]' (old): {check_a}")
    logger.debug(f"  [trace] check '[DEEP ANALYSIS —' (real header): {check_b}")
    logger.debug(f"  [trace] check 'deep analysis' in first 500 chars: {check_c}")
    logger.debug(f"  [trace] check 'deep analysis' in full message: {check_d}")
    logger.debug(f"  [trace] ctx.deep_analysis: {len(ctx.deep_analysis)} chars")
    if ctx.deep_analysis:
        logger.debug(f"  [trace] content preview: {ctx.deep_analysis[:300]}")
    else:
        logger.debug("  [trace] (not present)")
    logger.info("=" * 60)

"""Command dispatch — parses /ai-* commands from GitHub comments and routes to pipelines.

This is the single entry point for all comment-triggered AI commands.
Adding a new command = adding a handler here. No YAML changes needed.
Adding a new engine = adding it to _ENGINES here. No YAML changes needed.

Outputs (written to $GITHUB_OUTPUT for YAML job routing):
  command: review | retro | nudge | noop
  engine:  gpt | claude
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

from ..infra.constants import TEMPLATES_DIR
from ..infra.container import Container
from ..log import logger


# ── Command + engine registry ────────────────────────────────────

_COMMAND_RE = re.compile(r"^/ai-(\S+)(.*)", re.MULTILINE)
_ENGINE_RE = re.compile(r"engine:\s*(\S+)", re.IGNORECASE)
_DEEP_RE = re.compile(r"\bdeep\b", re.IGNORECASE)
_NODEEP_RE = re.compile(r"\bnodeep\b", re.IGNORECASE)
_FORCE_RE = re.compile(r"--force\b", re.IGNORECASE)

_COMMANDS = {"review", "retro", "remedy", "revert"}
_ENGINES = {"gpt", "claude", "none"}
_DEFAULT_ENGINE = "gpt"

# Commands that ALWAYS run on the claude engine, ignoring engine: hints in the
# comment. /ai-remedy needs Claude Code Action because remediation requires
# code edits, git push, and a final APPROVE — none of which the GPT engine does.
_CLAUDE_ONLY_COMMANDS = {"remedy"}

# Commands that run pure-Python (no LLM, no Claude). /ai-revert is git-only:
# finds every commit authored by the XAIR App on the PR branch and reverts it.
# No tokens beyond the GitHub App token, no API spend, deterministic.
_NO_ENGINE_COMMANDS = {"revert"}

# /ai-remedy approve: the comment text includes the literal word "approve" as
# the first token after `/ai-remedy`. Without it, the remedy run pushes commits
# but does NOT submit an APPROVE review (Bernard's request 2026-04-30).
_APPROVE_RE = re.compile(r"\bapprove\b", re.IGNORECASE)

_COMMAND_META = {
    "review": {"icon": "🤖", "label": "Review"},
    "retro":  {"icon": "🔍", "label": "Retro"},
    "remedy": {"icon": "🛠️", "label": "Remedy"},
    "revert": {"icon": "⏮️", "label": "Revert"},
}


# ── GitHub Actions output ────────────────────────────────────────

def _set_output(key: str, value: str) -> None:
    """Write to $GITHUB_OUTPUT for downstream job routing."""
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    logger.debug(f"[output] {key}={value}")


# ── Template loading ─────────────────────────────────────────────

@lru_cache(maxsize=8)
def _load_template(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


# ── Ack helper ───────────────────────────────────────────────────

def _ack_comment(container: Container, command: str, engine: str, pr_num: str, run_id: str) -> None:
    """Post acknowledgment reply to the trigger comment."""
    repo = os.environ.get("REPO", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

    meta = _COMMAND_META.get(command, {"icon": "⚙️", "label": command.title()})
    engine_tag = f" ({engine})" if engine != _DEFAULT_ENGINE else ""
    body = _load_template("ack").format(
        icon=meta["icon"], label=meta["label"] + engine_tag,
        run_id=run_id, run_url=run_url,
    )

    try:
        container.github.run_gh(
            "api", f"repos/{repo}/issues/{pr_num}/comments",
            "--method", "POST", "-f", f"body={body}",
            check=False,
        )
    except Exception:
        pass


# ── Nudge (pull_request opened) ──────────────────────────────────

def _handle_pr_opened(container: Container) -> None:
    """Post instructions on new PRs."""
    repo = os.environ.get("REPO", "")
    pr_num = os.environ.get("PR_NUM", "")
    if not repo or not pr_num:
        return

    body = _load_template("nudge")
    if not body:
        return

    container.github.run_gh(
        "api", f"repos/{repo}/issues/{pr_num}/comments",
        "--method", "POST", "-f", f"body={body}",
        check=False,
    )
    logger.info(f"Posted nudge on PR #{pr_num}")


# ── Parse helpers ────────────────────────────────────────────────

def _parse_engine(text: str) -> str:
    """Extract engine from text like 'engine:claude'. Returns default if not found."""
    match = _ENGINE_RE.search(text)
    if match:
        engine = match.group(1).lower()
        if engine in _ENGINES:
            return engine
        logger.warning(f"Unknown engine '{engine}' — falling back to {_DEFAULT_ENGINE}")
    return _DEFAULT_ENGINE


def _parse_guidance(tail: str, after_match: str) -> str:
    """Extract freeform guidance text, stripping engine: parameter."""
    continuation = []
    for line in after_match.splitlines():
        if line.strip().startswith("/ai-"):
            break
        if line.strip():
            continuation.append(line.strip())

    raw = (tail + " " + " ".join(continuation)).strip() if (tail or continuation) else ""
    # Strip engine:, deep, nodeep params from guidance text
    cleaned = _ENGINE_RE.sub("", raw)
    cleaned = _NODEEP_RE.sub("", cleaned)
    cleaned = _DEEP_RE.sub("", cleaned)
    return cleaned.strip()


# ── Remedy-cache detection (skip Claude SDK if prior run was clean) ──

_REMEDY_CLEAN_MARKER_RE = re.compile(
    r"<!--\s*ai-remedy:clean:([0-9a-f]{7,40})\s*-->",
    re.IGNORECASE,
)


def _has_prior_clean_remedy(container: "Container", repo: str, pr_num: str) -> bool:
    """True if a prior XAIR remedy review on the CURRENT HEAD declared the PR clean.

    Reads:
      1. Current PR head SHA via `gh pr view`
      2. Last 30 reviews on the PR
      3. Filters for reviews authored by `xair-xair-org-ai-reviewer[bot]`
      4. Looks for the hidden marker `<!-- ai-remedy:clean:<sha> -->` in body
      5. Marker SHA must match current HEAD (any new commit invalidates the cache)

    This is best-effort — failures fall through to running the full remedy.
    """
    try:
        head_view = container.github.run_gh(
            "pr", "view", pr_num, "--repo", repo, "--json", "headRefOid",
            check=False,
        )
        import json as _json
        head_sha = _json.loads(head_view).get("headRefOid", "")
        if not head_sha:
            return False

        reviews_raw = container.github.run_gh(
            "api", f"repos/{repo}/pulls/{pr_num}/reviews?per_page=30",
            check=False,
        )
        reviews = _json.loads(reviews_raw)
        if not isinstance(reviews, list):
            return False

        for review in reversed(reviews):
            login = (review.get("user") or {}).get("login", "")
            if login != "xair-xair-org-ai-reviewer[bot]":
                continue
            if review.get("state") != "COMMENTED":
                continue
            body = review.get("body") or ""
            match = _REMEDY_CLEAN_MARKER_RE.search(body)
            if not match:
                continue
            marker_sha = match.group(1).lower()
            if head_sha.lower().startswith(marker_sha) or marker_sha.startswith(head_sha.lower()):
                logger.info(
                    f"Found clean-remedy marker on review {review.get('id')} "
                    f"(sha={marker_sha[:7]}) matching current HEAD {head_sha[:7]}"
                )
                return True

        return False
    except Exception as exc:
        logger.warning(f"_has_prior_clean_remedy failed — falling through to full remedy: {exc}")
        return False


def _submit_approve_directly(container: "Container", repo: str, pr_num: str) -> None:
    """Submit an APPROVE review directly (no Claude). Used when prior /ai-remedy
    on the same HEAD was clean — saves a full SDK invocation.
    """
    body = (
        "✅ Approved via `/ai-remedy approve` cache hit — a previous "
        "`/ai-remedy` run on this commit declared the PR clean (no code changes "
        "needed). Skipping the Claude SDK invocation and submitting the approval "
        "directly. Re-push to invalidate the cache.\n\n"
        "_To force a fresh remedy + approve, push any commit and re-run "
        "`/ai-remedy approve`._"
    )
    container.github.run_gh(
        "api", f"repos/{repo}/pulls/{pr_num}/reviews",
        "--method", "POST",
        "-f", "event=APPROVE",
        "-f", f"body={body}",
        check=False,
    )
    logger.info(f"Direct APPROVE submitted on PR #{pr_num}")


# ── Command handlers ─────────────────────────────────────────────

def _run_review_gpt(
    container: Container,
    deep_mode: object | None = None,
    skip_dedup: bool = False,
) -> None:
    """Invoke the GPT-based review pipeline.

    Routes through the F-bridge v2 review declarative pipeline (Executor +
    8 stages) via run_review_full_via_executor. The bridge is a drop-in for
    pipelines.review.run_review — see PR #47 for parity (write_trace + dedup
    early-exit + same publisher selection + hallucinated finding filter).

    This is the single entry point for /ai-review GitHub comment dispatch —
    when this function changes, /ai-review behavior changes in production.

    Multi-perspective opt-in: setting ``VAIR_REVIEW_MULTI_PERSPECTIVE=1`` in
    the environment routes to the adversarial Codex+Claude pipeline
    (``run_review_multi_perspective_via_executor``). Default is OFF — the
    single-engine pipeline ships unchanged until the multi-perspective
    variant is validated in production. Plan v7 §6b.
    """
    from ..config.review import DeepMode, ReviewConfig

    mode = deep_mode if isinstance(deep_mode, DeepMode) else DeepMode.AUTO
    cfg = ReviewConfig.from_env(deep_mode=mode, skip_dedup=skip_dedup)

    if os.environ.get("VAIR_REVIEW_MULTI_PERSPECTIVE") == "1":
        from ..pipelines.review_via_executor import (
            run_review_multi_perspective_via_executor,
        )
        logger.info("Review variant: MULTI-PERSPECTIVE (adversarial Codex+Claude)")
        run_review_multi_perspective_via_executor(container, cfg)
        return

    from ..pipelines.review_via_executor import run_review_full_via_executor
    run_review_full_via_executor(container, cfg)


def _run_retro(container: Container, guidance: str = "") -> None:
    from ..config.retro import RetroConfig
    from ..pipelines.retro import run_retro
    run_retro(container, RetroConfig.from_env(guidance=guidance))


# ── Main dispatch ────────────────────────────────────────────────

def run_dispatch(container: Container) -> None:
    """Parse the GitHub event, decide command + engine, route or output for YAML.

    Writes to $GITHUB_OUTPUT:
      command=review|retro|nudge|noop
      engine=gpt|claude

    For engine=gpt: executes the Python pipeline directly.
    For engine=claude: outputs only (YAML runs ClaudeSDKAgentRunner via
    `python -m xair claude-review` in a separate job).
    """
    event_name = os.environ.get("EVENT_NAME", "")
    comment_body = os.environ.get("COMMENT_BODY", "")
    pr_num = os.environ.get("PR_NUM", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")

    # Handle PR opened → nudge
    if event_name == "pull_request":
        _set_output("command", "nudge")
        _set_output("engine", "none")
        _handle_pr_opened(container)
        return

    # Parse command
    match = _COMMAND_RE.search(comment_body)
    if not match:
        _set_output("command", "noop")
        _set_output("engine", "none")
        logger.info("No /ai-* command found in comment — no-op")
        return

    command = match.group(1).lower()
    tail = (match.group(2) or "").strip()
    after_match = comment_body[match.end():]

    if command not in _COMMANDS:
        _set_output("command", "noop")
        _set_output("engine", "none")
        logger.info(f"Unrecognized command: /ai-{command} — no-op")
        return

    # Parse engine, deep mode, and guidance from the full text
    from ..config.review import DeepMode

    full_text = tail + " " + after_match
    engine = _parse_engine(full_text)
    # /ai-remedy always runs on Claude — engine: hints in the comment are ignored.
    if command in _CLAUDE_ONLY_COMMANDS:
        engine = "claude"
    # /ai-revert is pure git, no LLM — engine is "none" so the YAML claude job skips.
    elif command in _NO_ENGINE_COMMANDS:
        engine = "none"
    guidance = _parse_guidance(tail, after_match)
    # /ai-remedy approve: detect the literal token in the tail (the chunk right
    # after the command). approve_mode flips the prompt's APPROVE-or-stop branch.
    approve_mode = bool(_APPROVE_RE.search(tail))

    # Tri-state deep mode: nodeep > deep > auto
    if _NODEEP_RE.search(full_text):
        deep_mode = DeepMode.DISABLED
    elif _DEEP_RE.search(full_text):
        deep_mode = DeepMode.FORCED
    else:
        deep_mode = DeepMode.AUTO

    _set_output("command", command)
    _set_output("engine", engine)
    _set_output("deep_mode", deep_mode.value)
    _set_output("guidance", guidance)  # forwarded to retro job

    mode_tag = ""
    if deep_mode != DeepMode.AUTO:
        mode_tag = f" {deep_mode.value}"
    if engine != _DEFAULT_ENGINE:
        mode_tag += f" engine={engine}"

    logger.info(f"Dispatching: /ai-{command}{mode_tag} on PR #{pr_num}")
    if guidance:
        logger.info(f"Operator guidance: {guidance[:200]}")

    # Ack
    _ack_comment(container, command, engine, pr_num, run_id)

    # Route: Python-executed commands run here. YAML-routed engines just output.
    if command == "review" and engine == "claude":
        # Pure Claude engine mode (legacy — separate YAML job)
        repo = os.environ.get("REPO", "")
        variant = os.environ.get("PROMPT_VARIANT", "frontend")
        from ..prompt.claude_builder import build_claude_prompt
        claude_prompt = build_claude_prompt(repo, pr_num, variant)
        prompt_path = "/tmp/claude-prompt.md"
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(claude_prompt)
        logger.info(f"Claude prompt built: {len(claude_prompt)} chars")
        logger.info(f"Engine: claude — YAML claude-review job will execute.")
        return

    if command == "remedy":
        repo = os.environ.get("REPO", "")
        variant = os.environ.get("PROMPT_VARIANT", "frontend")

        # Optimization: if /ai-remedy approve and a prior /ai-remedy run on the
        # current PR HEAD already marked the PR as `clean` (no remedy needed),
        # skip the Claude SDK invocation entirely and submit APPROVE directly.
        # The prior COMMENT review carries the marker `ai-remedy:clean:<sha>`;
        # we match on `<sha>` == current HEAD to invalidate when new commits
        # were pushed after the prior remedy.
        if approve_mode and _has_prior_clean_remedy(container, repo, pr_num):
            logger.info(
                "Prior /ai-remedy on this HEAD was clean — skipping Claude SDK, "
                "submitting APPROVE directly."
            )
            _submit_approve_directly(container, repo, pr_num)
            _set_output("command", "noop")
            _set_output("engine", "none")
            return

        from ..prompt.claude_remedy_builder import write_claude_remedy_prompt_file
        prompt_path = write_claude_remedy_prompt_file(
            repo, pr_num, variant, guidance, approve_mode=approve_mode
        )
        logger.info(
            f"Claude remedy prompt written: {prompt_path} (approve_mode={approve_mode})"
        )
        if os.environ.get("VAIR_REMEDY_MULTI_PERSPECTIVE") == "1":
            logger.info(
                "Remedy variant: MULTI-PERSPECTIVE (adversarial Codex+Claude) — "
                "the YAML claude-remedy job's ``xair remedy`` invocation "
                "will route through run_remedy_multi_perspective_via_executor."
            )
        else:
            logger.info("Remedy variant: single-engine (Claude SDK)")
        logger.info("Engine: claude — YAML claude-remedy job will execute.")
        return

    if command == "revert":
        repo = os.environ.get("REPO", "")
        from ..pipelines.revert import run_revert
        run_revert(container, repo, pr_num)
        return

    # --force flag: bypass dedup check
    force = bool(_FORCE_RE.search(full_text))

    # GPT pipeline (deep mode = auto/forced/disabled)
    if command == "review":
        _run_review_gpt(container, deep_mode=deep_mode, skip_dedup=force)
    elif command == "retro":
        _run_retro(container, guidance=guidance)

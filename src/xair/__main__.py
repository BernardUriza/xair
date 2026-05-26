"""CLI entry point — ``python -m xair <command>``."""

from __future__ import annotations

import sys

# Force UTF-8 stdout on Windows (cp1252 default crashes on LLM unicode output).
# Python 3.15 will make this the default (PEP 686). Until then, explicit.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from typing import Annotated

import typer

from .log import logger
from .domain.exceptions import AIReviewerError

from . import __version__

app = typer.Typer(
    name="xair",
    help=f"XAIR v{__version__} \u2014 xair-org Artificial Intelligence Reviewer",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.command()
def review(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print review to stdout instead of posting to GitHub")] = False,
) -> None:
    """Run the full AI review pipeline (env: REPO, PR_NUM, PROMPT_FILE).

    Uses the F-bridge v2 review declarative pipeline (Executor + 8 stages) via
    run_review_full_via_executor. The bridge is a drop-in for the
    pipelines.review.run_review monolith — see PR #47 for the parity
    (write_trace + dedup early-exit + same publisher selection).

    Multi-perspective opt-in: setting ``VAIR_REVIEW_MULTI_PERSPECTIVE=1``
    routes to the adversarial Codex+Claude pipeline. Same flag honored by
    dispatch._run_review_gpt — keeping both entry points in sync.
    """
    import os

    from .config import ReviewConfig
    from .infra.container import Container

    cfg = ReviewConfig.from_env(dry_run=dry_run)

    if os.environ.get("VAIR_REVIEW_MULTI_PERSPECTIVE") == "1":
        from .pipelines.review_via_executor import (
            run_review_multi_perspective_via_executor,
        )
        logger.info("Review variant: MULTI-PERSPECTIVE (adversarial Codex+Claude)")
        run_review_multi_perspective_via_executor(Container.production(), cfg)
        return

    from .pipelines.review_via_executor import run_review_full_via_executor
    run_review_full_via_executor(Container.production(), cfg)


@app.command()
def changelog() -> None:
    """Generate changelog from commits (env: BASE_REF, REPO)."""
    from .config import ChangelogConfig
    from .infra.container import Container
    from .pipelines.changelog import run_changelog
    run_changelog(Container.production(), ChangelogConfig.from_env())


@app.command()
def preflight() -> None:
    """Generate pre-deploy warning signal post (env: BASE_REF, REPO,
    TARGET_SERVICE, TARGET_TIME, SLACK_WEBHOOK_URL, PROMPT_FILE).

    Distinct from changelog — this fires BEFORE the deploy, with
    customer-impact one-liner + risk flags, posted as Slack Block Kit.
    Tracked: <ticket-id>.
    """
    from .config import PreflightConfig
    from .infra.container import Container
    from .pipelines.preflight import run_preflight
    run_preflight(Container.production(), PreflightConfig.from_env())


@app.command()
def retro() -> None:
    """Post-review retrospective — analyze PR outcomes, propose candidate learnings."""
    from .config import RetroConfig
    from .infra.container import Container
    from .pipelines.retro import run_retro
    run_retro(Container.production(), RetroConfig.from_env())


@app.command()
def dispatch() -> None:
    """Route /ai-* commands from GitHub comments to the correct pipeline."""
    from .infra.container import Container
    from .pipelines.dispatch import run_dispatch
    run_dispatch(Container.production())


@app.command()
def remedy() -> None:
    """Run /ai-remedy via Python SDK (replaces claude-code-action@v1 invocation).

    Reads prompt from PROMPT_FILE env (default /tmp/claude-prompt.md, written
    by the dispatch job), runs ClaudeSDKAgentRunner with write-capable tools,
    emits a rich Job Summary. The agent itself posts the final review event
    (APPROVE/COMMENT/REQUEST_CHANGES) via Bash(gh api ... reviews) per the
    remedy prompt contract — this command does NOT post reviews directly.

    Required env: REPO, PR_NUM, CLAUDE_CODE_OAUTH_TOKEN, GITHUB_TOKEN
    (or GH_TOKEN). Optional: PROMPT_FILE, TARGET_REPO_PATH, REMEDY_MAX_TURNS,
    APPROVE_MODE.

    Multi-perspective opt-in: setting ``VAIR_REMEDY_MULTI_PERSPECTIVE=1``
    routes to the adversarial Codex+Claude pipeline
    (``run_remedy_multi_perspective_via_executor``). Default OFF — the
    single-engine ``run_remedy`` ships unchanged until the variant is
    validated in production. Plan v7 §6b twin.
    """
    import os
    from .pipelines.remedy import RemedyConfig, run_remedy

    cfg = RemedyConfig.from_env()
    if os.environ.get("VAIR_REMEDY_MULTI_PERSPECTIVE") == "1":
        from .pipelines.remedy_via_executor import (
            run_remedy_multi_perspective_via_executor,
        )
        run_remedy_multi_perspective_via_executor(cfg)
        return
    run_remedy(cfg)


@app.command("claude-review")
def claude_review() -> None:
    """Run /ai-review (Claude engine) via Python SDK.

    Replaces the ``anthropics/claude-code-action@v1`` step in the
    ``claude-review`` job of ``ai-dispatch.yml``. Mirrors ``remedy``: reads
    the prompt artifact written by the dispatch job, runs
    ``ClaudeSDKAgentRunner`` with a read-only tool set, posts findings
    via ``gh api`` from inside the agent, and emits a rich Job Summary.

    Required env: REPO, PR_NUM, CLAUDE_CODE_OAUTH_TOKEN, GITHUB_TOKEN
    (or GH_TOKEN). Optional: PROMPT_FILE, TARGET_REPO_PATH, REVIEW_MAX_TURNS.
    """
    from .pipelines.claude_review import ClaudeReviewConfig, run_claude_review

    cfg = ClaudeReviewConfig.from_env()
    run_claude_review(cfg)


@app.command("local-review")
def local_review(
    repo: Annotated[str, typer.Option(help="GitHub repo, e.g. xair-org/frontend-core-2.0")],
    pr: Annotated[str, typer.Option(help="PR number")],
    variant: Annotated[str, typer.Option(help="Prompt variant")] = "frontend",
    deep: Annotated[bool, typer.Option("--deep", help="Force Claude deep analysis")] = False,
    nodeep: Annotated[bool, typer.Option("--nodeep", help="Force GPT-only, skip auto-deep")] = False,
) -> None:
    """Dry-run review locally — auto-resolves secrets, writes to logs/."""
    from .config.review import DeepMode
    if nodeep:
        deep_mode = DeepMode.DISABLED
    elif deep:
        deep_mode = DeepMode.FORCED
    else:
        deep_mode = DeepMode.AUTO
    from .services.local_runner import run_local_review
    run_local_review(repo=repo, pr_num=pr, variant=variant, deep_mode=deep_mode)


@app.command()
def resolve(
    issue: Annotated[str, typer.Option("--issue", help="Tracker issue ID (e.g., <ticket-id>)")],
    repo: Annotated[str, typer.Option("--repo", help="Target repo (e.g., xair-org/frontend-core-2.0)")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip push/PR/tracker updates")] = False,
) -> None:
    """Resolve a tracker issue: read context, run agent, open PR.

    Uses the F-bridge v2 resolve declarative pipeline (Executor + 11 stages) via
    run_resolve_full_via_executor. The bridge is a verified drop-in for the
    pipelines.resolve.run_resolve monolith — see PR #39 for the parity guarantees
    (ResolveTrace + tracker failure + re-raise + git diff/log capture).

    Workspace is read from WORKSPACE / GITHUB_WORKSPACE env vars (set by
    ai-resolve.yml workflow), with "." as the last-resort fallback for local
    invocations.

    Multi-perspective opt-in: setting ``VAIR_RESOLVE_MULTI_PERSPECTIVE=1``
    routes to the adversarial Codex+Claude pipeline
    (``run_resolve_multi_perspective_via_executor``). Default OFF — the
    single-engine ``run_resolve_full_via_executor`` ships unchanged until
    the variant is validated in production. Plan v7 §6b twin.
    """
    import os

    from .config.resolve import ResolveConfig
    from .infra.container import Container

    cfg = ResolveConfig.from_env(issue_id=issue, repo=repo, dry_run=dry_run)
    workspace = os.environ.get(
        "WORKSPACE", os.environ.get("GITHUB_WORKSPACE", ".")
    )

    if os.environ.get("VAIR_RESOLVE_MULTI_PERSPECTIVE") == "1":
        from .pipelines.resolve_multi_perspective_via_executor import (
            run_resolve_multi_perspective_via_executor,
        )
        logger.info("Resolve variant: MULTI-PERSPECTIVE (adversarial Codex+Claude)")
        run_resolve_multi_perspective_via_executor(
            Container.resolve_mode(), cfg, workspace
        )
        return

    from .pipelines.resolve_via_executor import run_resolve_full_via_executor
    run_resolve_full_via_executor(Container.resolve_mode(), cfg, workspace)


@app.command("issue-rank")
def issue_rank(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print ranking to stdout")] = False,
) -> None:
    """Rank open tracker issues by priority, readiness, and feasibility."""
    from .config.issue_rank import IssueRankConfig
    from .infra.container import Container
    from .pipelines.issue_rank import run_issue_rank
    run_issue_rank(Container.resolve_mode(), IssueRankConfig.from_env(dry_run=dry_run))


@app.command("orchestrate-plan")
def orchestrate_plan(
    issue: Annotated[int, typer.Option("--issue", help="Umbrella GitHub Issue number to plan against.")],
    issue_repo: Annotated[str, typer.Option("--issue-repo", help="owner/repo where the umbrella Issue lives. Default: xair-org/.github.")] = "xair-org/.github",
    workspace: Annotated[str, typer.Option("--workspace", help="Path to checked-out primary repo (the planner reads, never writes).")] = "",
    max_turns: Annotated[int, typer.Option("--max-turns", help="Cap on planner agent turns.")] = 20,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print plan markdown to stdout, do not post comment.")] = False,
) -> None:
    """Generate a VairPlan for an umbrella Issue and post it as a comment.

    Stage 1 of the orchestrator: read the Issue from ``issue-repo``, ask
    Claude to decompose it into a DAG of granular XAIR sub-commands
    (each step declares its own target repo for multi-repo plans),
    validate against the VairPlan schema, and post as a comment for
    human approval via /ai-run.

    ``primary-repo`` is the repo cloned into ``workspace`` for grounding
    the planner agent. For multi-repo plans, pick the repo whose code
    structure best informs the decomposition.
    """
    import os

    from .orchestrator.planner import (
        PlannerInput,
        fetch_issue,
        format_plan_markdown,
        post_plan_comment,
        run_planner,
    )

    ws = workspace or os.environ.get("WORKSPACE", os.environ.get("GITHUB_WORKSPACE", "."))
    title, body = fetch_issue(issue, issue_repo)

    if not body.strip():
        raise AIReviewerError(
            f"Issue #{issue} in {issue_repo} has an empty body — nothing to plan against."
        )

    out = run_planner(
        PlannerInput(
            issue_number=issue,
            issue_repo=issue_repo,
            issue_title=title,
            issue_body=body,
            workspace=ws,
        ),
        max_turns=max_turns,
    )

    markdown = format_plan_markdown(out.plan, turns=out.turns, cost_usd=out.cost_usd)

    if dry_run:
        print(markdown)
        return

    url = post_plan_comment(out.plan, markdown)
    logger.info(f"[orchestrate-plan] posted plan comment: {url or '(no url returned)'}")


@app.command("orchestrate-run")
def orchestrate_run(
    issue: Annotated[int, typer.Option("--issue", help="Umbrella GitHub Issue number whose plan to execute.")],
    issue_repo: Annotated[str, typer.Option("--issue-repo", help="owner/repo where the umbrella Issue lives. Default: xair-org/.github.")] = "xair-org/.github",
    workspaces: Annotated[list[str], typer.Option("--workspace", help="Per-repo checkout in the form owner/repo=/abs/path (repeat once per target repo).")] = [],
) -> None:
    """Execute the most recent VairPlan posted on the umbrella Issue.

    Stage 2 of the orchestrator: load the plan from the umbrella Issue's
    latest XAIR comment, traverse the DAG by topological waves, run a
    write-capable agent per ai-resolve step (in the workspace matching
    that step's target repo), commit + push + open a draft PR per step.
    Posts a final summary on the umbrella Issue.

    For multi-repo plans, pass ``--workspace`` once per target repo:
    ``--workspace xair-org/frontend-core-2.0=/wks/fe --workspace xair-org/xair-org-gen-backend=/wks/be``.

    Pre-req: the planner stage (``orchestrate-plan``) must have run on
    this Issue and posted a plan comment. If absent, this command aborts.
    """
    import os

    from .orchestrator.executor import execute_plan
    from .orchestrator.plan_loader import PlanNotFoundError, load_latest_plan
    from .orchestrator.token_refresh import TokenRefresher

    try:
        plan = load_latest_plan(issue, issue_repo)
    except PlanNotFoundError as e:
        raise AIReviewerError(str(e))

    if plan.issue != issue or plan.issue_repo != issue_repo:
        raise AIReviewerError(
            f"Loaded plan does not match CLI args: "
            f"plan=({plan.issue_repo}#{plan.issue}) cli=({issue_repo}#{issue})"
        )

    # Parse --workspace repo=path pairs into a dict for the multi-repo executor.
    ws_map: dict[str, str] = {}
    for pair in workspaces:
        if "=" not in pair:
            raise AIReviewerError(
                f"--workspace must be owner/repo=/abs/path; got {pair!r}"
            )
        repo, path = pair.split("=", 1)
        ws_map[repo.strip()] = path.strip()

    # Backward-compat: when no --workspace pairs given, fall back to WORKSPACE
    # / GITHUB_WORKSPACE for single-repo plans (executor's _resolve_workspaces
    # will reject if the plan actually spans multiple repos).
    if not ws_map:
        fallback = os.environ.get("WORKSPACE", os.environ.get("GITHUB_WORKSPACE", "."))
        if plan.default_repo is not None:
            ws_map = {plan.default_repo: fallback}

    logger.info(
        f"[orchestrate-run] loaded plan — {len(plan.steps)} step(s), "
        f"{len(plan.topological_waves())} wave(s)"
    )

    # Mint a refresher only when both App credentials are present. Falls back
    # to whatever GH_TOKEN the workflow set — fine for short runs, will fail
    # on long ones that cross the 1h installation-token expiry.
    refresher: TokenRefresher | None = None
    if os.environ.get("VAIR_APP_ID") and os.environ.get("VAIR_APP_PRIVATE_KEY"):
        refresher = TokenRefresher.from_env()
        logger.info("[orchestrate-run] token refresher armed (XAIR App credentials present)")
    else:
        logger.warning(
            "[orchestrate-run] token refresher OFF — VAIR_APP_ID / VAIR_APP_PRIVATE_KEY "
            "not in env. Long runs (>1h) will fail mid-execution on auth."
        )

    result = execute_plan(plan, ws_map, refresher=refresher)

    if result.aborted:
        raise AIReviewerError(
            f"Orchestrator aborted: {result.abort_reason} "
            f"({len(result.succeeded_steps)} succeeded, {len(result.failed_steps)} failed)"
        )


@app.command("local-retro")
def local_retro(
    repo: Annotated[str, typer.Option(help="GitHub repo, e.g. xair-org/frontend-core-2.0")],
    pr: Annotated[str, typer.Option(help="PR number")],
    variant: Annotated[str, typer.Option(help="Prompt variant")] = "frontend",
    guidance: Annotated[str, typer.Option(help="Optional operator guidance")] = "",
) -> None:
    """Retrospective analysis locally — auto-resolves secrets, writes to logs/."""
    from .services.local_runner import run_local_retro
    run_local_retro(repo=repo, pr_num=pr, variant=variant, guidance=guidance)


def main() -> None:
    try:
        app()
    except AIReviewerError as e:
        logger.error(f"::error::{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

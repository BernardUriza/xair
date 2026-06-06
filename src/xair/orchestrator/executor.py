"""Executor — traverses a XairPlan DAG dispatching agents per step.

For each step in topological order:
  1. Check out a fresh branch off the step's target-repo base branch
  2. Run a write-capable Claude agent with the step's spec as the user prompt
  3. Substance gate: refuse to push if zero Edit/Write calls
  4. Commit + push + open a draft PR with ``Part of <issue_repo>#<source_issue>``

Multi-repo (PoC v3+): each ``XairStep`` declares its own ``repo``. The
executor receives a ``workspaces: dict[str, str]`` mapping ``owner/repo``
to a local checkout path; before running a step it switches to the
workspace matching that step's repo. Steps in different repos land PRs
in their own repos but share the umbrella source Issue (which may live
in yet another repo — typically ``xair-org/.github``).

The executor does NOT update Plane — orchestrator-driven runs are scoped to
GitHub Issues per ``issue-workflow.md`` (tech-debt lives in GH Issues, not
Plane). The Plane integration in ``pipelines/resolve.py`` is intentionally
unused here to keep the orchestrator decoupled from the tracker layer.

PoC v3 limits (lifted in later phases):
- Sequential execution. Parallel-within-wave needs matrix jobs — phase 4.
- Only ``ai-resolve`` step type. ``ai-review``/``ai-remedy`` arrive as a
  follow-up — those reduce to a single ``gh pr comment`` per step once a
  PR from a prior ``ai-resolve`` exists.
- Abort on first step failure. Independent-step continuation is phase 4.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Literal

from ..infra.agent_runner import ClaudeSDKAgentRunner
from ..log import logger
from .plan import StepType, XairPlan, XairStep
from .token_refresh import TokenRefresher


StepStatus = Literal["success", "failure", "skipped"]


@dataclass
class StepResult:
    step_id: str
    status: StepStatus
    pr_url: str = ""
    branch: str = ""
    error: str = ""
    edit_calls: int = 0
    turns: int = 0
    cost_usd: float = 0.0


@dataclass
class ExecutionResult:
    plan_issue: int
    issue_repo: str  # where the umbrella Issue lives (e.g. xair-org/.github)
    steps: list[StepResult] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""

    @property
    def succeeded_steps(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == "success"]

    @property
    def failed_steps(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == "failure"]


def _detect_base_branch(repo: str) -> str:
    """Mirror of ResolveConfig.base_branch auto-detect heuristic."""
    return "staging-v2" if "backend" in repo else "main"


def _branch_name(source_issue: int, step: XairStep) -> str:
    safe_id = re.sub(r"[^a-zA-Z0-9-]+", "-", step.id).strip("-").lower()
    return f"xair-orch/issue-{source_issue}/{safe_id}"


def _short_pr_title(step: XairStep, source_issue: int) -> str:
    """First sentence of the step spec, capped + sanitized for a PR title."""
    first_line = step.spec.split("\n", 1)[0].strip()
    # Drop trailing punctuation, cap at 60 chars.
    title_body = first_line.rstrip(".:;").replace('"', "'")
    if len(title_body) > 60:
        title_body = title_body[:57].rstrip() + "..."
    return f"xair({step.id}): {title_body} (#{source_issue})"


def _run_git(*args: str, cwd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def _apply_fresh_token(refresher: TokenRefresher | None, workspace: str) -> None:
    """Mint a fresh installation token and propagate it to git + gh CLI.

    No-op when refresher is None — used by tests that mock auth out.
    Idempotent: safe to call before every push, PR creation, and gh api call.

    Auth flows through the remote URL (``https://x-access-token:TOKEN@github.com/...``)
    — the same shape the workspace clone uses. We deliberately do NOT touch
    ``http.extraheader``: combining a URL credential with an extraheader makes
    git send two conflicting auth signals and GitHub rejects subsequent
    fetch/push with ``remote: invalid credentials``. We also clear any stale
    extraheader left over from actions/checkout-style setups for the same
    reason.
    """
    if refresher is None:
        return

    token = refresher.token()
    # gh CLI reads GH_TOKEN / GITHUB_TOKEN env on every invocation.
    os.environ["GH_TOKEN"] = token
    os.environ["GITHUB_TOKEN"] = token

    # Read the current remote URL, strip any existing credentials, embed the
    # fresh token. Works whether the URL is bare (https://github.com/owner/repo.git)
    # or already credentialed (https://x-access-token:OLD@github.com/owner/repo.git).
    current = _run_git("remote", "get-url", "origin", cwd=workspace).stdout.strip()
    bare_url = re.sub(r"^https://[^@/]*@", "https://", current)
    new_url = bare_url.replace("https://", f"https://x-access-token:{token}@", 1)
    _run_git("remote", "set-url", "origin", new_url, cwd=workspace)

    # Clear any extraheader left by actions/checkout or the executor workflow
    # to avoid the dual-auth collision. --unset-all is idempotent — it succeeds
    # whether the key is present or absent (exit 5 on absent, which check=False
    # tolerates).
    _run_git(
        "config",
        "--local",
        "--unset-all",
        "http.https://github.com/.extraheader",
        cwd=workspace,
        check=False,
    )


def _checkout_step_branch(step: XairStep, source_issue: int, base_branch: str, workspace: str) -> str:
    """Reset workspace to a clean base + create the step's feature branch.

    Aborts hard if the branch already exists locally — the caller must
    handle re-runs explicitly (delete the old branch or pick a new id).
    """
    branch = _branch_name(source_issue, step)
    # Explicit refspec: a shallow clone of a non-default branch leaves no
    # ``refs/remotes/origin/<base>`` ref, so a bare ``git fetch origin <base>``
    # only writes ``FETCH_HEAD`` and the subsequent checkout fails with
    # ``pathspec '<base>' did not match``.
    _run_git(
        "fetch", "origin",
        f"{base_branch}:refs/remotes/origin/{base_branch}",
        cwd=workspace,
    )
    _run_git("checkout", "-B", base_branch, f"origin/{base_branch}", cwd=workspace)
    _run_git("reset", "--hard", f"origin/{base_branch}", cwd=workspace)
    _run_git("clean", "-fd", cwd=workspace)
    # -B creates or resets the branch — idempotent across retries within
    # the same workflow run.
    _run_git("checkout", "-B", branch, cwd=workspace)
    return branch


def _commit_and_push(step: XairStep, source_issue: int, branch: str, workspace: str) -> None:
    """Stage everything, commit if dirty, push the branch."""
    status = _run_git("status", "--porcelain", cwd=workspace).stdout.strip()
    if status:
        _run_git("add", "-A", cwd=workspace)
        msg = f"xair({step.id}): orchestrator step for issue #{source_issue}"
        _run_git("commit", "-m", msg, cwd=workspace)
    # Plain --force (not --force-with-lease): the shallow clone has no local
    # knowledge of the xair-orch/* ref, so lease checks always fail as
    # "stale info" when a prior run pushed the same step branch (e.g. after
    # a successful push followed by a downstream failure that aborted the
    # run). These branches are owned exclusively by the orchestrator —
    # no human collaboration to protect against.
    _run_git("push", "-u", "origin", branch, "--force", cwd=workspace)


def _open_pr(
    step: XairStep,
    source_issue: int,
    source_repo: str,
    base_branch: str,
    workspace: str,
    head_branch: str,
) -> str:
    """Open a draft PR for the step's branch. Returns the PR URL."""
    title = _short_pr_title(step, source_issue)
    body = (
        f"Part of #{source_issue}.\n\n"
        f"## Step {step.id}\n\n"
        f"{step.spec}\n\n"
        f"---\n"
        f"_Generated by XAIR Orchestrator — executor stage._"
    )

    body_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    )
    body_file.write(body)
    body_file.close()

    try:
        # ``--head`` is passed explicitly because ``gh pr create`` infers it
        # from the workspace's current branch via tracking config, which can
        # be absent or ambiguous when the remote URL embeds credentials
        # (``https://x-access-token:TOKEN@github.com/...``) — gh fails with
        # ``you must first push the current branch to a remote, or use the
        # --head flag`` even after a successful push.
        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--draft",
                "--base", base_branch,
                "--head", head_branch,
                "--title", title,
                "--body-file", body_file.name,
                "--repo", source_repo,
            ],
            capture_output=True, text=True, encoding="utf-8",
            cwd=workspace,
        )
    finally:
        os.unlink(body_file.name)

    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    urls = [line for line in combined.splitlines() if line.startswith("https://github.com/")]
    if not urls:
        raise RuntimeError(
            f"gh pr create produced no URL for step {step.id}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return urls[0]


def _run_agent_for_step(
    step: XairStep, base_branch: str, workspace: str, system_prompt: str
) -> tuple[int, int, float, str]:
    """Invoke the Claude SDK agent on this step's spec. Returns (edit_calls, turns, cost_usd, error).

    ``system_prompt`` is supplied by the consumer — it encodes the consumer's
    conventions (e.g. tsc gate, minimal-change, no AI attribution). xair is
    framework-only and must not embed a consumer's prompt; the orchestrator's
    step spec replaces what an issue body would have provided.

    error is empty on success.
    """
    user_prompt = (
        f"## Step {step.id}\n\n"
        f"You are executing one step of a multi-step decomposition plan.\n"
        f"Branch: `{_branch_name_from_step(step)}` (already checked out for you).\n"
        f"Base: `{base_branch}`.\n\n"
        f"## Instruction\n\n"
        f"{step.spec}\n\n"
        f"## Constraints\n\n"
        f"- Do NOT run `npm install` or any package-manifest mutation.\n"
        f"- Run `npx tsc --noEmit` before stopping. Fix every error.\n"
        f"- Do NOT commit — the orchestrator will commit and open a PR after you.\n"
        f"- Make the SMALLEST diff that fulfills the instruction. Avoid scope creep.\n"
    )

    runner = ClaudeSDKAgentRunner(
        model="claude-sonnet-4-6",
        allowed_tools=("Read", "Edit", "Bash", "Glob", "Grep", "Write"),
        permission_mode="bypassPermissions",
    )

    outcome = runner.run(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        cwd=workspace,
        max_turns=80,
    )

    if outcome.error:
        return outcome.edit_calls, outcome.turns, outcome.total_cost_usd, outcome.error
    return outcome.edit_calls, outcome.turns, outcome.total_cost_usd, ""


def _branch_name_from_step(step: XairStep) -> str:
    """Used inside the agent prompt for context only — the executor controls the actual branch."""
    return f"xair-orch/.../{step.id}"


def _post_summary_comment(result: ExecutionResult, refresher: TokenRefresher | None = None, workspace: str = ".") -> None:
    """Post a final status comment on the source Issue."""
    succeeded = result.succeeded_steps
    failed = result.failed_steps

    lines: list[str] = [
        f"🤖 **XAIR Orchestrator — Execution Report** (Issue #{result.plan_issue})",
        "",
    ]
    if result.aborted:
        lines.append(f"⚠️ **Aborted:** {result.abort_reason}")
        lines.append("")

    lines.append(f"**{len(succeeded)} succeeded** / **{len(failed)} failed** of {len(result.steps)} step(s) attempted.")
    lines.append("")

    if succeeded:
        lines.append("## PRs opened")
        lines.append("")
        for s in succeeded:
            lines.append(f"- `{s.step_id}` → {s.pr_url} (turns: {s.turns}, cost: ${s.cost_usd:.4f})")
        lines.append("")

    if failed:
        lines.append("## Failures")
        lines.append("")
        for s in failed:
            lines.append(f"- `{s.step_id}` — {s.error[:300]}")
        lines.append("")

    total_cost = sum(s.cost_usd for s in result.steps)
    total_turns = sum(s.turns for s in result.steps)
    lines.extend([
        "---",
        "",
        f"<sub>Total turns: {total_turns} · Total cost: ${total_cost:.4f}</sub>",
    ])

    body = "\n".join(lines)
    _apply_fresh_token(refresher, workspace)
    subprocess.run(
        [
            "gh", "issue", "comment", str(result.plan_issue),
            "--repo", result.issue_repo,
            "--body-file", "-",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _resolve_workspaces(
    plan: XairPlan, workspaces: dict[str, str] | str
) -> dict[str, str]:
    """Normalize the workspaces argument into a dict keyed by ``owner/repo``.

    Accepts either:
    - a dict mapping each target repo to its local checkout path, or
    - a single string path (legacy single-repo callers) — promoted to a dict
      keyed by ``plan.default_repo``.

    Raises if any step's ``repo`` lacks a workspace entry.
    """
    if isinstance(workspaces, str):
        if plan.default_repo is None:
            raise ValueError(
                "execute_plan received a single workspace path but the plan "
                "has no default_repo and steps span multiple repos. Pass a "
                "dict[str, str] mapping each step.repo to its checkout path."
            )
        workspaces = {plan.default_repo: workspaces}

    missing = plan.target_repos - workspaces.keys()
    if missing:
        raise ValueError(
            f"execute_plan is missing workspace entries for: {sorted(missing)}. "
            f"Provided: {sorted(workspaces.keys())}. "
            f"Plan target repos: {sorted(plan.target_repos)}."
        )
    return workspaces


def _abort(
    result: ExecutionResult,
    step_result: StepResult,
    reason: str,
    *,
    refresher: TokenRefresher | None,
    workspace: str,
) -> ExecutionResult:
    """Record a failed step, mark the run aborted, post the summary, and return.

    Centralizes the abort path shared by every failure point in ``execute_plan``:
    log the error, append the ``StepResult``, set ``aborted``/``abort_reason``,
    post the summary comment on the source Issue, and hand ``result`` back so the
    caller can ``return _abort(...)`` in a single line.
    """
    logger.error(f"[executor] {step_result.step_id} — {step_result.error}")
    result.steps.append(step_result)
    result.aborted = True
    result.abort_reason = reason
    _post_summary_comment(result, refresher=refresher, workspace=workspace)
    return result


def execute_plan(
    plan: XairPlan,
    workspaces: dict[str, str] | str,
    *,
    system_prompt: str,
    refresher: TokenRefresher | None = None,
) -> ExecutionResult:
    """Run every step in topological order. Abort on first failure.

    ``workspaces`` maps each target repo (``owner/repo``) in the plan to
    the local path of a fresh checkout. A single string is accepted for
    backward-compat with single-repo plans (uses ``plan.default_repo`` as
    the key).

    ``system_prompt`` is the consumer-supplied agent system prompt, passed
    through to every step's agent run. xair is framework-only and ships no
    prompt of its own — the consumer (bair, etc.) owns its conventions.

    When ``refresher`` is provided, mints a fresh GitHub App installation
    token before every push, PR creation, and summary comment. Required for
    runs that may cross the 1-hour token expiry boundary. Without a
    refresher, falls back to whatever GH_TOKEN was set at workflow start —
    fine for short test runs, will fail mid-execution on real workloads.
    """
    workspace_map = _resolve_workspaces(plan, workspaces)
    # Use the first workspace as a token-refresh target for issue-scoped gh
    # CLI calls (summary comment). Any path works because env vars are global
    # and per-repo .git/config is re-applied before each repo's git ops.
    issue_workspace = next(iter(workspace_map.values()))

    waves = plan.topological_waves()
    result = ExecutionResult(plan_issue=plan.issue, issue_repo=plan.issue_repo)

    logger.info(
        f"[executor] starting — issue={plan.issue} issue_repo={plan.issue_repo} "
        f"steps={len(plan.steps)} waves={len(waves)} "
        f"target_repos={sorted(plan.target_repos)} "
        f"refresher={'on' if refresher else 'off'}"
    )

    for w_idx, wave in enumerate(waves, start=1):
        logger.info(f"[executor] wave {w_idx}/{len(waves)} — {len(wave)} step(s)")
        for step in wave:
            if step.type != StepType.AI_RESOLVE:
                logger.warning(
                    f"[executor] skipping step {step.id} — type {step.type.value} "
                    f"not supported in PoC v3 (ai-resolve only)"
                )
                result.steps.append(StepResult(
                    step_id=step.id, status="skipped",
                    error=f"step type {step.type.value} not implemented in PoC v3",
                ))
                continue

            assert step.repo is not None, (
                "XairPlan validator should have resolved step.repo before execution"
            )
            step_workspace = workspace_map[step.repo]
            step_base_branch = _detect_base_branch(step.repo)

            logger.info(
                f"[executor] running step {step.id} → "
                f"repo={step.repo} base={step_base_branch}"
            )
            try:
                _apply_fresh_token(refresher, step_workspace)
                branch = _checkout_step_branch(
                    step, plan.issue, step_base_branch, step_workspace
                )
            except subprocess.CalledProcessError as e:
                err = f"branch setup failed: {e.stderr or e.stdout or e}"
                return _abort(
                    result,
                    StepResult(step_id=step.id, status="failure", error=err),
                    f"step {step.id} branch setup failed",
                    refresher=refresher,
                    workspace=issue_workspace,
                )

            edit_calls, turns, cost, agent_err = _run_agent_for_step(
                step, step_base_branch, step_workspace, system_prompt
            )

            if agent_err:
                err = f"agent crashed: {agent_err}"
                return _abort(
                    result,
                    StepResult(
                        step_id=step.id, status="failure", branch=branch,
                        error=err, edit_calls=edit_calls, turns=turns, cost_usd=cost,
                    ),
                    f"step {step.id} agent crashed",
                    refresher=refresher,
                    workspace=issue_workspace,
                )

            if edit_calls == 0:
                err = "substance gate: agent invoked zero Edit/Write calls"
                return _abort(
                    result,
                    StepResult(
                        step_id=step.id, status="failure", branch=branch,
                        error=err, edit_calls=0, turns=turns, cost_usd=cost,
                    ),
                    f"step {step.id} substance gate failed",
                    refresher=refresher,
                    workspace=issue_workspace,
                )

            try:
                _apply_fresh_token(refresher, step_workspace)
                _commit_and_push(step, plan.issue, branch, step_workspace)
            except subprocess.CalledProcessError as e:
                err = f"commit/push failed: {e.stderr or e.stdout or e}"
                return _abort(
                    result,
                    StepResult(
                        step_id=step.id, status="failure", branch=branch,
                        error=err, edit_calls=edit_calls, turns=turns, cost_usd=cost,
                    ),
                    f"step {step.id} commit/push failed",
                    refresher=refresher,
                    workspace=issue_workspace,
                )

            try:
                _apply_fresh_token(refresher, step_workspace)
                pr_url = _open_pr(
                    step, plan.issue, step.repo, step_base_branch, step_workspace, branch
                )
            except RuntimeError as e:
                err = f"PR creation failed: {e}"
                return _abort(
                    result,
                    StepResult(
                        step_id=step.id, status="failure", branch=branch,
                        error=err, edit_calls=edit_calls, turns=turns, cost_usd=cost,
                    ),
                    f"step {step.id} PR creation failed",
                    refresher=refresher,
                    workspace=issue_workspace,
                )

            logger.info(f"[executor] {step.id} — PR: {pr_url}")
            result.steps.append(StepResult(
                step_id=step.id, status="success", branch=branch, pr_url=pr_url,
                edit_calls=edit_calls, turns=turns, cost_usd=cost,
            ))

    _post_summary_comment(result, refresher=refresher, workspace=issue_workspace)
    logger.info(
        f"[executor] done — {len(result.succeeded_steps)} succeeded, "
        f"{len(result.failed_steps)} failed"
    )
    return result

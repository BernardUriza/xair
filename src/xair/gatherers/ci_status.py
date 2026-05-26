"""CI status gatherer — fetch failing checks and tail of failed-step logs.

Surfaces test/build failures to the reviewer LLM so it can flag broken CI
in the review and tie findings to specific failing checks. Reads from
GitHub's check-runs API at the PR's head SHA.
"""

from __future__ import annotations

import json

from ..domain.models import PRIdentifier
from ..infra.constants import CI_STATUS
from ..contracts import ActionsIO, FileStore, GitHubClient


_FAILING_CONCLUSIONS = {"failure", "timed_out", "action_required", "startup_failure"}
_MAX_LOG_TAIL_LINES = 40
_MAX_FAILING_CHECKS_DETAILED = 3


def _get_head_sha(pr: PRIdentifier, github: GitHubClient) -> str:
    return github.run_gh(
        "api", f"repos/{pr.repo}/pulls/{pr.number}",
        "--jq", ".head.sha", check=False,
    ).strip()


def _list_check_runs(github: GitHubClient, repo: str, sha: str) -> list[dict]:
    raw = github.run_gh(
        "api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100",
        check=False,
    )
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return payload.get("check_runs", []) or []


def _tail_failed_log(github: GitHubClient, repo: str, run_id: int) -> str:
    """Get the tail of failed steps for a workflow run. Best-effort, may return empty."""
    raw = github.run_gh(
        "run", "view", str(run_id),
        "--repo", repo,
        "--log-failed",
        check=False,
    )
    if not raw.strip():
        return ""
    lines = raw.splitlines()
    tail = lines[-_MAX_LOG_TAIL_LINES:] if len(lines) > _MAX_LOG_TAIL_LINES else lines
    return "\n".join(tail)


def _format_status(checks: list[dict], github: GitHubClient, repo: str) -> str:
    failing = [c for c in checks if (c.get("conclusion") or "") in _FAILING_CONCLUSIONS]
    in_progress = [c for c in checks if c.get("status") == "in_progress"]
    succeeded = [
        c for c in checks if (c.get("conclusion") or "") in {"success", "neutral", "skipped"}
    ]

    lines: list[str] = []
    lines.append(
        f"Summary: {len(failing)} failing, {len(in_progress)} in progress, "
        f"{len(succeeded)} succeeded/skipped (total {len(checks)})."
    )

    if not failing:
        lines.append("")
        lines.append("All checks are green or pending. No CI blockers visible at this commit.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Failing checks:")
    for c in failing:
        name = c.get("name", "?")
        url = c.get("html_url") or c.get("details_url") or ""
        conclusion = c.get("conclusion", "?")
        lines.append(f"  - {name} [{conclusion}] {url}")

    # Pull tail logs for the first N failing checks (cheap signal, expensive call)
    detailed = failing[:_MAX_FAILING_CHECKS_DETAILED]
    if detailed:
        lines.append("")
        lines.append(f"Failed-step log tails (last {_MAX_LOG_TAIL_LINES} lines per check):")
        for c in detailed:
            run_id = (c.get("details_url") or "").split("/runs/")[-1].split("/")[0]
            try:
                run_id_int = int(run_id)
            except ValueError:
                continue
            tail = _tail_failed_log(github, repo, run_id_int)
            if not tail:
                continue
            lines.append("")
            lines.append(f"--- {c.get('name', '?')} (run {run_id_int}) ---")
            lines.append(tail)

    if len(failing) > _MAX_FAILING_CHECKS_DETAILED:
        lines.append("")
        lines.append(
            f"({len(failing) - _MAX_FAILING_CHECKS_DETAILED} more failing checks omitted from log tail)"
        )

    return "\n".join(lines)


def gather_ci_status(
    pr: PRIdentifier, github: GitHubClient, store: FileStore, actions: ActionsIO,
) -> str:
    """Return formatted CI status for the PR's head SHA. Empty if no checks visible."""
    head_sha = _get_head_sha(pr, github)
    if not head_sha:
        actions.warning("CI status: could not resolve head SHA -- skipping")
        store.write(CI_STATUS, "")
        return ""

    checks = _list_check_runs(github, pr.repo, head_sha)
    if not checks:
        actions.notice("CI status: no check runs visible at head SHA -- skipping")
        store.write(CI_STATUS, "")
        return ""

    text = _format_status(checks, github, pr.repo)
    failing_count = sum(1 for c in checks if (c.get("conclusion") or "") in _FAILING_CONCLUSIONS)
    actions.notice(f"CI status: {len(checks)} check(s), {failing_count} failing")
    store.write(CI_STATUS, text)
    return text

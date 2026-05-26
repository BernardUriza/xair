"""Get PR diff with truncation support."""

from __future__ import annotations

from ..infra.constants import DIFF_INPUT, ENV_TRUNCATED
from ..domain.models import PRIdentifier
from ..contracts import ActionsIO, FileStore, GitHubClient
from ..log import logger


# ── Fetch ────────────────────────────────────────────────────────


def _fetch_diff(pr: PRIdentifier, github: GitHubClient, actions: ActionsIO) -> str:
    """Fetch PR diff via ``gh pr diff``, falling back to per-file patches."""
    diff_text = github.run_gh("pr", "diff", str(pr.number), "--repo", pr.repo, check=False)

    if diff_text.strip():
        logger.debug("Got diff via gh pr diff")
        return diff_text

    actions.warning("PullRequest.diff too large — assembling from per-file patches")
    patches: list[str] = []
    for page in range(1, 31):
        batch = github.run_gh(
            "api",
            f"repos/{pr.repo}/pulls/{pr.number}/files?per_page=100&page={page}",
            "--jq", ".[].patch // empty",
            check=False,
        )
        if not batch.strip():
            break
        patches.append(batch)
    return "\n".join(patches)


# ── Truncate ─────────────────────────────────────────────────────


def _apply_truncation(
    diff_text: str, max_bytes: int, store: FileStore, actions: ActionsIO,
) -> tuple[str, bool]:
    """Truncate diff if over budget. Returns (text, was_truncated)."""
    diff_bytes = len(diff_text.encode("utf-8"))
    logger.debug(f"Diff size: {diff_bytes} bytes (max: {max_bytes})")

    if diff_bytes > max_bytes:
        truncated = diff_text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        store.write(DIFF_INPUT, truncated)
        actions.set_env(ENV_TRUNCATED, "true")
        actions.warning(f"Diff truncated from {diff_bytes} to {max_bytes} bytes")
        return truncated, True

    store.write(DIFF_INPUT, diff_text)
    actions.set_env(ENV_TRUNCATED, "false")
    return diff_text, False


# ── Public API ───────────────────────────────────────────────────


def gather_diff(
    pr: PRIdentifier,
    max_diff_bytes: int,
    github: GitHubClient,
    store: FileStore,
    actions: ActionsIO,
) -> tuple[str, bool]:
    """Fetch + truncate PR diff. Returns (diff_text, was_truncated)."""
    diff_text = _fetch_diff(pr, github, actions)
    return _apply_truncation(diff_text, max_diff_bytes, store, actions)

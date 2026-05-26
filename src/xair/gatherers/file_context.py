"""Fetch full file contents for files changed in the PR (hybrid context)."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from ..domain.invariants import SKIP_EXTENSIONS, SKIP_FILES
from ..infra.constants import (
    DEFAULT_MAX_LINES_PER_FILE,
    DEFAULT_MAX_TOTAL_FILE_BYTES,
    FULL_FILES_CONTEXT,
)
from ..domain.models import PRIdentifier
from ..contracts import ActionsIO, FileStore, GitHubClient


# ── Models ───────────────────────────────────────────────────────


@dataclass
class FetchStats:
    fetched: int = 0
    skipped_size: int = 0
    total_bytes: int = 0


# ── List + Filter ────────────────────────────────────────────────


def _list_changed_files(pr: PRIdentifier, github: GitHubClient) -> list[tuple[str, str]]:
    """Get (filename, status) tuples from the PR."""
    raw = github.run_gh(
        "api", f"repos/{pr.repo}/pulls/{pr.number}/files?per_page=100",
        "--jq", '.[] | "\\(.filename)\t\\(.status)"',
        check=False,
    )
    entries = []
    for line in raw.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            entries.append((parts[0].strip(), parts[1].strip()))
    return entries


def _filter_fetchable(entries: list[tuple[str, str]]) -> list[str]:
    """Remove binaries, lock files, and deleted files."""
    return [
        name for name, status in entries
        if status != "removed"
        and not any(name.endswith(ext) for ext in SKIP_EXTENSIONS)
        and name.split("/")[-1] not in SKIP_FILES
    ]


def _get_head_sha(pr: PRIdentifier, github: GitHubClient) -> str:
    """Get the PR head commit SHA."""
    return github.run_gh(
        "api", f"repos/{pr.repo}/pulls/{pr.number}",
        "--jq", ".head.sha", check=False,
    ).strip()


# ── Fetch + Assemble ─────────────────────────────────────────────


def _fetch_file(github: GitHubClient, repo: str, filename: str, sha: str) -> str | None:
    """Fetch a single file's content. Returns None if unavailable."""
    raw = github.run_gh(
        "api", f"repos/{repo}/contents/{filename}?ref={sha}",
        "--jq", ".content", check=False,
    )
    content_b64 = raw.strip().replace("\n", "")
    if not content_b64:
        return None
    try:
        return base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except Exception:
        return None


def _assemble_files(
    files: list[str], github: GitHubClient, repo: str, sha: str,
) -> tuple[list[str], FetchStats]:
    """Download files up to byte budget. Returns (blocks, stats)."""
    stats = FetchStats()
    blocks: list[str] = []
    budget = DEFAULT_MAX_TOTAL_FILE_BYTES

    for filename in files:
        if stats.total_bytes >= budget:
            remaining = len(files) - stats.fetched - stats.skipped_size
            blocks.append(f"\n[TRUNCATED — {remaining} files omitted, 80KB context limit reached]\n")
            break

        content = _fetch_file(github, repo, filename, sha)
        if content is None:
            continue

        line_count = content.count("\n") + 1
        if line_count > DEFAULT_MAX_LINES_PER_FILE:
            stats.skipped_size += 1
            continue

        block = f"[FULL FILE: {filename}] ({line_count} lines)\n{content}\n---\n"
        block_bytes = len(block.encode("utf-8"))

        if stats.total_bytes + block_bytes > budget:
            remaining_bytes = budget - stats.total_bytes - 200
            if remaining_bytes > 1000:
                truncated = content.encode("utf-8")[:remaining_bytes].decode("utf-8", errors="ignore")
                block = (
                    f"[FULL FILE: {filename}] (TRUNCATED — {line_count} lines, "
                    f"showing first ~{remaining_bytes} bytes)\n{truncated}\n[...truncated...]\n---\n"
                )
                blocks.append(block)
                stats.total_bytes += len(block.encode("utf-8"))
                stats.fetched += 1
            break

        blocks.append(block)
        stats.total_bytes += block_bytes
        stats.fetched += 1

    return blocks, stats


# ── Public API ───────────────────────────────────────────────────


def gather_file_context(
    pr: PRIdentifier, github: GitHubClient, store: FileStore, actions: ActionsIO,
) -> str:
    """Fetch full file contents for non-binary changed files."""
    entries = _list_changed_files(pr, github)

    if not entries:
        actions.notice("No files found in PR — skipping full file fetch")
        store.write(FULL_FILES_CONTEXT, "")
        return ""

    files = _filter_fetchable(entries)

    if not files:
        actions.notice("No fetchable files in PR — skipping full file fetch")
        store.write(FULL_FILES_CONTEXT, "")
        return ""

    head_sha = _get_head_sha(pr, github)
    if not head_sha:
        actions.warning("Could not get PR head SHA — skipping full file fetch")
        store.write(FULL_FILES_CONTEXT, "")
        return ""

    actions.notice(f"Fetching {len(files)} files from head ref {head_sha[:8]}")
    blocks, stats = _assemble_files(files, github, pr.repo, head_sha)

    if not blocks:
        actions.notice("No file contents could be fetched")
        store.write(FULL_FILES_CONTEXT, "")
        return ""

    text = "\n".join(blocks)
    store.write(FULL_FILES_CONTEXT, text)
    actions.notice(
        f"Full file context: {stats.fetched} files fetched, "
        f"{stats.skipped_size} skipped (>{DEFAULT_MAX_LINES_PER_FILE} lines), "
        f"{stats.total_bytes} bytes total"
    )
    return text

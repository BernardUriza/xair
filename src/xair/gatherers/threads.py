"""Query resolved/unresolved review threads from the PR."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..infra.constants import RESOLVED_CONTEXT
from ..infra.graphql import load_query
from ..domain.models import PRIdentifier
from ..infra.constants import TEMPLATES_DIR
from ..contracts import ActionsIO, FileStore, GitHubClient

_TEMPLATES = TEMPLATES_DIR


# ── Models ───────────────────────────────────────────────────────


@dataclass
class ThreadInfo:
    file: str
    line: int | None
    finding: str
    reply: str = ""

    def format_resolved(self) -> str:
        first = self.finding.split("\n")[0]
        reply = self.reply.split("\n")[0]
        return f'- [{self.file}:{self.line}] {first} → Dev: "{reply}..."'

    def format_unresolved(self) -> str:
        first = self.finding.split("\n")[0]
        return f"- [{self.file}:{self.line}] {first}"


@dataclass
class ThreadClassification:
    resolved: list[ThreadInfo] = field(default_factory=list)
    unresolved: list[ThreadInfo] = field(default_factory=list)


# ── Fetch ────────────────────────────────────────────────────────


def _fetch_threads(pr: PRIdentifier, github: GitHubClient) -> list[dict]:
    """Fetch raw thread nodes from the GitHub GraphQL API."""
    try:
        raw = github.run_gh(
            "api", "graphql",
            "-f", f"query={load_query('get_review_threads')}",
            "-f", f"owner={pr.owner}",
            "-f", f"repo={pr.name}",
            "-F", f"pr={pr.number}",
        )
        data = json.loads(raw)
    except Exception:
        data = {}

    return (
        data.get("data", {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviewThreads", {})
        .get("nodes", [])
    )


# ── Classify ─────────────────────────────────────────────────────


def _classify_threads(nodes: list[dict]) -> ThreadClassification:
    """Split bot threads into resolved and unresolved."""
    result = ThreadClassification()

    for thread in nodes:
        comments = thread.get("comments", {}).get("nodes", [])
        if not comments:
            continue
        first = comments[0]
        if first.get("author", {}).get("login") != "github-actions[bot]":
            continue

        info = ThreadInfo(
            file=first.get("path", ""),
            line=first.get("line"),
            finding=first.get("body", ""),
        )

        if thread.get("isResolved"):
            info.reply = comments[1].get("body", "no reply") if len(comments) > 1 else "no reply"
            result.resolved.append(info)
        else:
            result.unresolved.append(info)

    return result


# ── Format ───────────────────────────────────────────────────────


def _format_threads(classified: ThreadClassification) -> str:
    """Render classified threads into markdown context."""
    sections: list[str] = []

    if classified.resolved:
        tpl = (_TEMPLATES / "threads_resolved.md").read_text(encoding="utf-8")
        items = "\n".join(t.format_resolved() for t in classified.resolved)
        sections.append(tpl.format(items=items))

    if classified.unresolved:
        tpl = (_TEMPLATES / "threads_unresolved.md").read_text(encoding="utf-8")
        items = "\n".join(t.format_unresolved() for t in classified.unresolved)
        sections.append(tpl.format(items=items))

    return "\n".join(sections)


# ── Public API ───────────────────────────────────────────────────


def gather_threads(
    pr: PRIdentifier,
    github: GitHubClient,
    store: FileStore,
    actions: ActionsIO,
) -> str:
    """Fetch, classify, format, and store thread context."""
    nodes = _fetch_threads(pr, github)
    classified = _classify_threads(nodes)
    text = _format_threads(classified)

    actions.notice(f"Thread context: {len(classified.resolved)} resolved, {len(classified.unresolved)} unresolved")
    store.write(RESOLVED_CONTEXT, text)
    return text

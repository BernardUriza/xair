"""Publish strategy -- diff-aware publishers that never lose findings.

Each publisher knows HOW to deliver a review. The pipeline knows WHAT to
deliver. Adding Slack, file output, or a webhook = adding a Publisher, not
modifying the pipeline.

The GitHubPublisher uses DiffIndex to partition findings into inline-valid
(anchored to a line inside a hunk) and file-level (anchored to the body as
text). This prevents GitHub's atomic review API from rejecting the entire
batch when a single finding points outside the diff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .diff_index import DiffIndex, LineAnchor
from .models import Finding, PRIdentifier, ReviewResponse
from ..contracts import ActionsIO, GitHubClient
from ..log import logger


# -- Publisher protocol ------------------------------------------------

class ReviewPublisher(Protocol):
    """Delivers a formatted review to its destination."""

    def publish(
        self,
        review: ReviewResponse,
        pr: PRIdentifier,
        run_id: str,
        truncated: bool,
        max_diff_bytes: int,
        dropped_findings: int = 0,
        narrative: str = "",
        diff_text: str = "",
    ) -> None: ...


# -- Formatting (shared) -----------------------------------------------

_SEVERITY_EMOJI: dict[str, str] = {
    "critical": "\U0001f534",
    "warning": "\U0001f7e1",
    "info": "ℹ️",
}


def _severity_badge(sev: str) -> str:
    """Return 'emoji SEVERITY' for display."""
    emoji = _SEVERITY_EMOJI.get(sev, "ℹ️")
    return f"{emoji} {sev.upper()}"


def _render_file_level_section(findings: list[Finding]) -> str:
    """Render a markdown section listing findings that cannot be anchored inline.

    Either they lack a file, their line is outside the diff, or the inline
    batch was rejected and we demoted them. In every case we MUST preserve the
    content in the body -- the whole point of this module is that no finding
    disappears silently.
    """
    if not findings:
        return ""
    lines = ["", "### Findings (file-level)", ""]
    for f in findings:
        if f.file and f.line > 0:
            target = f"`{f.file}:{f.line}`"
        elif f.file:
            target = f"`{f.file}`"
        else:
            target = "*(no file)*"
        lines.append(f"**{_severity_badge(f.severity)}** {target} — {f.comment}")
        lines.append("")
    return "\n".join(lines)


def format_review_body(
    review: ReviewResponse,
    pr: PRIdentifier,
    run_id: str,
    truncated: bool,
    max_diff_bytes: int,
    narrative: str = "",
    file_level: list[Finding] | None = None,
    dropped_findings: int = 0,
) -> str:
    """Build the markdown body for a review.

    The body is XAIR's narrative voice plus any findings that could not be
    anchored inline. Inline-anchored findings appear as diff comments, not
    in the body.
    """
    run_url = f"https://github.com/{pr.repo}/actions/runs/{run_id}"
    count = len(review.findings)
    body_text = narrative or review.summary
    fl: list[Finding] = list(file_level) if file_level else []

    parts = [f"## {review.title}", "", body_text]

    if truncated:
        parts.append("")
        parts.append(
            f"> ⚠️ Diff truncated (>{max_diff_bytes} bytes). Some files not reviewed."
        )

    if count > 0:
        from collections import Counter
        sev_counts = Counter(f.severity.lower() for f in review.findings)
        badges = " · ".join(
            f"{_SEVERITY_EMOJI.get(s, chr(0x2139) + chr(0xfe0f))} {n} {s}"
            for s, n in sev_counts.most_common()
        )
        parts.extend(["", f"**{badges}**"])

    file_level_section = _render_file_level_section(fl)
    if file_level_section:
        parts.append(file_level_section)

    if dropped_findings > 0:
        parts.append("")
        parts.append(
            f"> {dropped_findings} finding(s) were filtered (referenced files outside the diff)."
        )

    from .. import __version__
    parts.extend(["", "---",
                  f"XAIR v{__version__} · [Trace]({run_url}) · {count} findings"])
    return "\n".join(parts)


# -- Implementations ---------------------------------------------------

@dataclass(slots=True)
class DryRunPublisher:
    """Prints the review to stdout. No side effects."""

    def publish(
        self,
        review: ReviewResponse,
        pr: PRIdentifier,
        run_id: str,
        truncated: bool,
        max_diff_bytes: int,
        dropped_findings: int = 0,
        narrative: str = "",
        diff_text: str = "",
    ) -> None:
        idx = DiffIndex(diff_text)
        inline_items, file_level = _partition(review.findings, idx, snap_window=0)
        body = format_review_body(
            review, pr, run_id, truncated, max_diff_bytes,
            narrative, file_level, dropped_findings,
        )
        logger.info("\n" + "=" * 60)
        logger.info("DRY RUN -- review NOT posted to GitHub")
        logger.info("=" * 60)
        logger.info(f"PR:         {pr.repo}#{pr.number}")
        logger.info(f"Title:      {review.title}")
        logger.info(f"Findings:   {len(review.findings)} "
                    f"({len(inline_items)} inline, {len(file_level)} file-level)")
        logger.info(f"Highlights: {len(review.highlights)}")
        logger.info(f"Summary:    {review.summary[:200]}...")
        if review.findings:
            logger.info("\n--- FINDINGS ---")
            for i, f in enumerate(review.findings, 1):
                logger.info(f"  [{i}] {_severity_badge(f.severity)} | {f.file}:{f.line}")
                logger.info(f"      {f.comment[:200]}")
        if review.highlights:
            logger.info("\n--- HIGHLIGHTS ---")
            for h in review.highlights:
                logger.info(f"  + {h.file}:{h.line} -- {h.comment[:150]}")
        logger.info("\n--- FULL REVIEW BODY ---")
        logger.info(body)
        logger.info("=" * 60 + "\n")


# -- Partition (pure, module-level so DryRun can reuse) ----------------

def _partition(
    findings: list[Finding],
    idx: DiffIndex,
    snap_window: int = 0,
) -> tuple[list[tuple[dict, Finding]], list[Finding]]:
    """Split findings into (inline_items, file_level).

    inline_items = [(payload_dict, finding), ...] where payload_dict has
    path/line/side/body ready for the GitHub comments[] array and finding
    is the original object so a failed batch can be demoted cleanly.

    file_level = findings that lack a file, lack a line, or point outside
    the diff (after optional snapping).
    """
    inline: list[tuple[dict, Finding]] = []
    file_level: list[Finding] = []
    for f in findings:
        if not f.file or f.line <= 0:
            file_level.append(f)
            continue
        anchor: LineAnchor | None = idx.snap(f.file, f.line, window=snap_window)
        if anchor is None:
            file_level.append(f)
            continue
        payload = {
            "path": f.file,
            "line": anchor.line,
            "side": anchor.side,
            "body": f"**{_severity_badge(f.severity)}**: {f.comment}",
        }
        inline.append((payload, f))
    return inline, file_level


@dataclass(slots=True)
class GitHubPublisher:
    """Posts the review to the PR via GitHub API, diff-aware and fail-safe.

    Flow:
      1. Parse the PR diff into a DiffIndex.
      2. Partition findings -> (inline_valid, file_level).
      3. POST one review with comments[] for inline_valid and file_level
         rendered into the body. This is the happy path.
      4. If GitHub 422s anyway (race with the diff fetch, or a DiffIndex gap),
         demote every inline finding to file-level and retry once. The retry
         is body-only, so it cannot fail for the same reason.

    Cost: 1 API call on success, 2 on fallback. No per-comment verification,
    no secondary rate limits.
    """

    github: GitHubClient
    actions: ActionsIO
    snap_window: int = 0

    def _post_api(self, endpoint: str, payload: dict) -> bool:
        """POST a review via gh api. Returns True on success."""
        result = self.github.run_gh(
            "api", endpoint,
            "--method", "POST", "--input", "-",
            check=False,
            input_data=json.dumps(payload),
        )
        output = result.strip()
        if not output:
            logger.warning("  API returned empty response")
            return False
        try:
            data = json.loads(output)
            if "id" in data:
                return True
            msg = data.get("message", "unknown")
            errors = data.get("errors", [])
            logger.warning(f"  API rejected: {msg}")
            if errors:
                logger.warning(f"  API errors: {json.dumps(errors)[:300]}")
            return False
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"  API raw response (not JSON): {output[:300]}")
            return False

    def publish(
        self,
        review: ReviewResponse,
        pr: PRIdentifier,
        run_id: str,
        truncated: bool,
        max_diff_bytes: int,
        dropped_findings: int = 0,
        narrative: str = "",
        diff_text: str = "",
    ) -> None:
        endpoint = f"repos/{pr.repo}/pulls/{pr.number}/reviews"
        idx = DiffIndex(diff_text)
        inline_items, file_level = _partition(
            review.findings, idx, snap_window=self.snap_window,
        )

        if review.findings and not diff_text:
            # Guardrail: we asked for diff-aware publishing but got nothing.
            # Everything demotes; at least the content survives in the body.
            self.actions.warning(
                "No diff_text provided to publisher -- every finding will be file-level"
            )

        # -- Happy path: atomic POST with partitioned inline payload -------
        body = format_review_body(
            review, pr, run_id, truncated, max_diff_bytes,
            narrative, file_level, dropped_findings,
        )
        payload: dict = {"body": body, "event": "COMMENT"}
        if inline_items:
            payload["comments"] = [item[0] for item in inline_items]
        if self._post_api(endpoint, payload):
            logger.info(
                f"Posted {len(inline_items)} inline + {len(file_level)} file-level "
                f"({review.title})"
            )
            return

        if not inline_items:
            # Body-only review POST rejected. Last resort: post the body as a
            # plain issue comment so the review output is never lost silently.
            self.actions.warning(
                "Body-only review POST rejected -- falling back to issue comment"
            )
            issue_endpoint = f"repos/{pr.repo}/issues/{pr.number}/comments"
            if self._post_api(issue_endpoint, {"body": body}):
                logger.info(
                    f"Posted as issue comment (review API rejected, {review.title})"
                )
            else:
                self.actions.error("Failed to post review -- body-only payload rejected")
            return

        # -- Fallback: demote inline to file-level, repost body-only -------
        # Cost: exactly one extra API call. No per-comment verify, so no
        # secondary rate limits. Content is preserved in the body.
        self.actions.warning(
            f"Inline atomic POST rejected despite DiffIndex partition -- "
            f"demoting {len(inline_items)} finding(s) to file-level and retrying"
        )
        all_file_level = list(file_level) + [item[1] for item in inline_items]
        body = format_review_body(
            review, pr, run_id, truncated, max_diff_bytes,
            narrative, all_file_level, dropped_findings,
        )
        if self._post_api(endpoint, {"body": body, "event": "COMMENT"}):
            logger.info(
                f"Posted 0 inline + {len(all_file_level)} file-level "
                f"(after fallback, {review.title})"
            )
        else:
            self.actions.error("Failed to post review -- all attempts rejected")


def check_existing_review(github: GitHubClient, pr: PRIdentifier) -> bool:
    """Return True if the bot already reviewed this commit."""
    try:
        latest_sha = github.run_gh(
            "api", f"repos/{pr.repo}/pulls/{pr.number}",
            "--jq", ".head.sha",
        ).strip()
    except Exception:
        return False

    if not latest_sha:
        return False

    try:
        existing = github.run_gh(
            "api", f"repos/{pr.repo}/pulls/{pr.number}/reviews",
            "--jq",
            f'[.[] | select((.user.login == "github-actions[bot]" '
            f'or .user.login == "xair-xair-org-ai-reviewer[bot]")'
            f' and .commit_id == "{latest_sha}")] | length',
        ).strip()
        return int(existing) > 0
    except Exception:
        return False

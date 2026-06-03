"""Load a previously-posted XairPlan from an Issue's most recent XAIR comment.

The planner stage posts plans as Issue comments with the JSON embedded inside
a ``<details>``/```json fenced block. The executor reads them back from there —
no separate persistence layer needed, the comment IS the storage.
"""

from __future__ import annotations

import json
import re
import subprocess

from .plan import XairPlan


_XAIR_AUTHOR = "xair-xair-org-ai-reviewer"
_PLAN_SIGNATURE = "Raw XairPlan JSON"
_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class PlanNotFoundError(RuntimeError):
    """No XAIR-authored plan comment exists on the target Issue."""


def load_latest_plan(issue: int, issue_repo: str) -> XairPlan:
    """Return the most recent XairPlan posted as a comment on (issue_repo, issue).

    ``issue_repo`` is where the umbrella Issue lives (e.g. ``xair-org/.github``),
    NOT the target repo of any particular step. Errors fatally when no plan
    exists — the executor cannot proceed without one. Re-run the planner
    stage if this happens.
    """
    proc = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(issue),
            "--repo",
            issue_repo,
            "--json",
            "comments",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data = json.loads(proc.stdout)
    comments = data.get("comments", [])

    # Walk most-recent-first.
    for comment in reversed(comments):
        author = (comment.get("author") or {}).get("login")
        body = comment.get("body") or ""
        if author != _XAIR_AUTHOR:
            continue
        if _PLAN_SIGNATURE not in body:
            continue

        match = _JSON_FENCE_RE.search(body)
        if not match:
            continue

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        return XairPlan.model_validate(payload)

    raise PlanNotFoundError(
        f"No XAIR-authored plan comment found on {issue_repo}#{issue}. "
        f"Run the planner first: gh workflow run ai-orchestrate.yml "
        f"-f issue={issue} -f issue-repo={issue_repo}"
    )

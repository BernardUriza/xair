"""Issue gatherer — fetch from the tracker + GPT readiness assessment."""

from __future__ import annotations

from ..domain.issue_scoring import ScoredIssue, score_priority, score_project, score_staleness
from ..domain.models import Issue
from ..infra.constants import OPENAI_MODEL
from ..contracts import LlmProvider
from ..log import logger


_READINESS_PROMPT = """You are evaluating whether a tracker issue has enough specification for an AI agent to autonomously implement it.

Rate the issue's READINESS on a scale from 0.0 to 1.0:
- 1.0: Crystal clear spec — acceptance criteria, specific files/components mentioned, expected behavior described
- 0.7: Good spec — clear intent, some details, an AI agent could figure out the rest from the codebase
- 0.4: Vague — general direction but missing specifics (which files, what behavior, what constraints)
- 0.1: One-liner title with no description — impossible to implement without asking questions

For each issue, return a JSON object: {"score": 0.0-1.0, "reason": "one sentence why"}

ISSUES:
{issues_text}

Return a JSON array with one object per issue, in the same order:
[{{"id": "VIS-XX", "score": 0.7, "reason": "..."}}]
"""


def gather_issues(
    issues: list[Issue],
    llm: LlmProvider,
) -> list[ScoredIssue]:
    """Transform TrackerIssues into ScoredIssues with readiness from GPT.

    Steps:
    1. Compute static scores (priority, project, staleness)
    2. Batch-assess readiness via GPT
    3. Return ScoredIssues ready for ranking
    """
    if not issues:
        return []

    # Step 1: Build ScoredIssues with static scores
    scored = []
    for issue in issues:
        s = ScoredIssue(
            identifier=issue.identifier,
            title=issue.title,
            description=issue.description,
            priority=0,  # not on TrackerIssue, will use label mapping
            priority_label="",
            state_name=issue.state_name,
            project_name=issue.project_name,
            assignee_name=issue.assignee_name,
            labels=issue.labels,
            git_branch_name=issue.git_branch_name,
            score_project=score_project(issue.project_name),
        )
        scored.append(s)

    # Step 2: GPT readiness (batch — one call for all issues)
    logger.info(f"  [issues] Assessing readiness for {len(scored)} issues via GPT...")
    issues_text = "\n---\n".join(
        f"ID: {s.identifier}\nTitle: {s.title}\nDescription: {(s.description or 'No description')[:500]}"
        for s in scored
    )
    try:
        result = llm.call(
            system="You are an issue readiness evaluator. Return valid JSON only.",
            user=_READINESS_PROMPT.format(issues_text=issues_text),
            model=OPENAI_MODEL,
            max_tokens=4000,
            temperature=0.1,
        )
        # GPT json_mode wraps arrays: {"issues": [...]} or {"results": [...]}
        items = result
        if isinstance(result, dict):
            items = (result.get("issues") or result.get("results")
                     or result.get("items") or result.get("data") or [])
        if isinstance(items, list):
            readiness_map = {r["id"]: r for r in items if "id" in r}
        else:
            readiness_map = {}
    except Exception as e:
        logger.warning(f"  [issues] GPT readiness failed: {e}")
        readiness_map = {}

    # Apply readiness scores
    for s in scored:
        r = readiness_map.get(s.identifier, {})
        s.score_readiness = float(r.get("score", 0.5))
        s.readiness_reason = r.get("reason", "No assessment available")

    # Step 3: Staleness (doesn't need GPT)
    # Note: created_at not on TrackerIssue model. Default to 0.
    for s in scored:
        s.score_staleness = 0.3  # placeholder until created_at is available

    # Feasibility stays at 0.0 for Phase 2 — Agent SDK in Phase 3
    for s in scored:
        s.score_feasibility = 0.5  # neutral default

    return scored

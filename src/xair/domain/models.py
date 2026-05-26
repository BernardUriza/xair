"""All domain models — frozen dataclasses + Pydantic for LLM output validation.

Single file, single source of truth. No model lives outside this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .exceptions import ValidationError

# ── PR Identity ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PRIdentifier:
    owner: str
    name: str
    number: int

    @property
    def repo(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def from_env(cls, repo: str, number: int | str) -> PRIdentifier:
        owner, name = repo.split("/", 1)
        return cls(owner=owner, name=name, number=int(number))


# ── LLM Review Output (validated) ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    file: str
    line: int
    comment: str


@dataclass(frozen=True, slots=True)
class Highlight:
    file: str
    line: int
    comment: str


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    type: str
    observation: str
    confidence: str


@dataclass(frozen=True, slots=True)
class ReviewResponse:
    title: str
    summary: str
    findings: list[Finding]
    highlights: list[Highlight]
    knowledge: list[KnowledgeItem]

    @classmethod
    def validate(cls, raw: dict) -> ReviewResponse:
        """Parse and validate LLM output. Raises ValidationError on bad data."""
        if "_raw" in raw:
            return cls.empty()

        try:
            findings = []
            for f in raw.get("findings", []):
                if not isinstance(f, dict):
                    raise ValidationError(f"Finding is not a dict: {f!r}")
                raw_sev = str(f.get("severity", "info")).lower().strip()
                # GPT sometimes prefixes with emoji (e.g. "🔴 critical") — extract the keyword
                sev = "info"
                for level in ("critical", "warning", "info"):
                    if level in raw_sev:
                        sev = level
                        break
                # Parse line safely — GPT sometimes returns "42-45" or "near 42"
                raw_line = f.get("line", 0)
                try:
                    line_num = max(0, int(raw_line))
                except (TypeError, ValueError):
                    # Extract first number from string like "42-45" or "near 42"
                    import re
                    m = re.search(r"\d+", str(raw_line))
                    line_num = int(m.group()) if m else 0

                # Accept both finding schemas:
                # - Single-engine GPT (prompts/base.md): {comment: "..."}
                # - Multi-perspective Claude synthesizer: {title, description, code_snippet?}
                # When `comment` is missing, fall back to combining
                # title + description into the comment field that the
                # publisher's formatter reads. Without this, the
                # synthesizer's findings rendered as
                # "**🔴 CRITICAL**: " with empty body on PR #1364.
                comment = str(f.get("comment", "")).strip()
                if not comment:
                    title = str(f.get("title", "")).strip()
                    description = str(f.get("description", "")).strip()
                    if title and description:
                        comment = f"**{title}** — {description}"
                    else:
                        comment = title or description or ""
                    snippet = str(f.get("code_snippet", "")).strip()
                    if snippet:
                        comment += f"\n\n```\n{snippet}\n```"
                findings.append(Finding(
                    severity=sev,
                    file=str(f.get("file", "")),
                    line=line_num,
                    comment=comment,
                ))

            highlights = []
            for h in raw.get("highlights", []):
                if not isinstance(h, dict):
                    continue
                highlights.append(Highlight(
                    file=str(h.get("file", "")),
                    line=max(0, int(h.get("line", 0))),
                    comment=str(h.get("comment", "")),
                ))

            knowledge = []
            for k in raw.get("knowledge", []):
                if not isinstance(k, dict):
                    continue
                knowledge.append(KnowledgeItem(
                    type=str(k.get("type", "?")),
                    observation=str(k.get("observation", "")),
                    confidence=str(k.get("confidence", "?")),
                ))

            return cls(
                title=str(raw.get("title", "Code Review")),
                summary=str(raw.get("summary", "")),
                findings=findings,
                highlights=highlights,
                knowledge=knowledge,
            )
        except (TypeError, ValueError, KeyError) as e:
            raise ValidationError(f"Invalid review JSON structure: {e}") from e

    @classmethod
    def empty(cls) -> ReviewResponse:
        return cls(
            title="Code Review",
            summary="Review produced invalid output.",
            findings=[], highlights=[], knowledge=[],
        )


# ── Tracker Issue (read-only, from GraphQL API — historically Linear) ─


@dataclass(frozen=True, slots=True)
class TrackerIssue:
    """An issue fetched from the tracker (historically Linear via GraphQL)."""
    id: str              # UUID (internal)
    identifier: str      # "VIS-123" (human-readable)
    title: str
    description: str
    state_name: str      # "Todo", "In Progress", etc.
    team_id: str
    team_key: str        # "VIS"
    git_branch_name: str # "bernarduriza/vis-123-slug"
    labels: list[str]
    project_name: str
    assignee_name: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowState:
    """A tracker workflow state (for status transitions; historically Linear)."""
    id: str
    name: str
    type: str  # "started", "completed", "unstarted", "cancelled"


# ── Parsed YAML policy entry (read from learnings/*.yml) ────────────


@dataclass(frozen=True, slots=True)
class PolicyEntry:
    """A parsed entry from the learnings YAML file (read-only)."""
    type: str
    rule: str
    boundary: str
    confidence: str


# ── Changelog Models ─────────────────────────────────────────────────


# Matches PR numbers in commit subjects emitted by GitHub:
#   - Squash merge:  "fix(scope): description (#1234)"  →  trailing "(#NNNN)"
#   - Merge commit:  "Merge pull request #1234 from xair-org/branch"
# A single regex with alternatives keeps both formats funneled to one capture group.
_PR_NUMBER_RE = re.compile(
    r"(?:\(#(?P<squash>\d+)\)\s*$)|(?:Merge pull request #(?P<merge>\d+)\b)"
)


@dataclass(frozen=True, slots=True)
class CommitEntry:
    sha: str
    message: str
    author: str
    date: str
    pr_number: int | None = None

    @classmethod
    def from_log_line(cls, line: str) -> CommitEntry:
        fields = line.split("|", 3)
        if len(fields) < 4:
            return cls(sha="", message=line, author="", date="")
        message = fields[1]
        pr_number = cls._extract_pr_number(message)
        return cls(
            sha=fields[0][:7],
            message=message,
            author=fields[2],
            date=fields[3],
            pr_number=pr_number,
        )

    @staticmethod
    def _extract_pr_number(message: str) -> int | None:
        match = _PR_NUMBER_RE.search(message)
        if not match:
            return None
        return int(match.group("squash") or match.group("merge"))

    def format_line(self) -> str:
        if not self.sha:
            return f"- {self.message}"
        pr_tag = f"(#{self.pr_number}) " if self.pr_number else ""
        return f"- [{self.sha}] {pr_tag}{self.message} (by {self.author}, {self.date})"


@dataclass(frozen=True, slots=True)
class ChangelogOutput:
    slack_message: str
    detailed_markdown: str

    @classmethod
    def from_dict(cls, d: dict) -> ChangelogOutput:
        return cls(
            slack_message=d.get("slack_message", "No slack message"),
            detailed_markdown=d.get("detailed_markdown", "No detailed markdown"),
        )


@dataclass(frozen=True, slots=True)
class PreflightOutput:
    """LLM output for the pre-deploy announcement (<ticket-id>).

    Four audience-distinct fields in one JSON blob:
    - customer_impact_oneliner: non-engineer language, for Tyler/Scarlett
    - engineer_summary: technical breakdown, for the rest of the dev team
    - risk_flags: explicit signals for the deploy operator (Bernard/Katie)
    - release_urgency: low|medium|high — surfaces "when does this need to
      ship" in the Slack card header, computed from commit-type keywords +
      age of the oldest queued PR.
    """
    customer_impact_oneliner: str
    engineer_summary: str
    risk_flags: list[str]
    release_urgency: str = "medium"  # low | medium | high — default conservative

    @classmethod
    def from_dict(cls, d: dict) -> PreflightOutput:
        raw_flags = d.get("risk_flags", [])
        if not isinstance(raw_flags, list):
            raw_flags = [str(raw_flags)] if raw_flags else []

        # Normalize urgency — LLM occasionally returns "HIGH", "high.",
        # "high urgency", or even an emoji. Coerce to the closed enum.
        raw_urgency = str(d.get("release_urgency", "medium")).strip().lower()
        urgency = "medium"
        for level in ("high", "medium", "low"):
            if level in raw_urgency:
                urgency = level
                break

        return cls(
            customer_impact_oneliner=str(
                d.get("customer_impact_oneliner", "No impact summary generated")
            ).strip(),
            engineer_summary=str(
                d.get("engineer_summary", "No engineer summary generated")
            ).strip(),
            risk_flags=[str(f).strip() for f in raw_flags if str(f).strip()],
            release_urgency=urgency,
        )


# ── Review Context (frozen — replaces mutable PipelineContext) ───────


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """Immutable context carrying only what formatters and the review pipeline need.

    Built incrementally in the pipeline via dataclasses.replace().
    Gathered text (learnings, rules, prior_art, etc.) stays in pipeline locals.
    """
    pr: PRIdentifier
    model: str
    max_diff_bytes: int
    prompt_file: str = ""
    diff: str = ""
    truncated: bool = False
    system_prompt: str = ""
    user_message: str = ""
    prompt_hash: str = ""
    review: ReviewResponse = field(default_factory=ReviewResponse.empty)
    # Context fields — consumed by trace_formatter and prompt builder
    selected_rules: str = ""
    learnings: str = ""
    rules: str = ""
    prior_art: str = ""
    resolved_threads: str = ""
    css_health: str = ""
    ci_status: str = ""
    deep_analysis: str = ""
    full_files: str = ""


@dataclass(frozen=True, slots=True)
class PlaneIssue:
    """A Plane issue fetched via REST API (Plane replaced Linear in April 2026).

    Mirrors TrackerIssue's shape so downstream pipelines (resolve, issue_rank) can
    accept either via duck-typing. Plane has no team scoping and no native
    branch-name generator — `team_key` is hardcoded to the project identifier
    ("<tracker-prefix>") and `git_branch_name` is generated deterministically from the
    sequence_id + slugified title.
    """
    id: str              # UUID (internal)
    identifier: str      # "<ticket-id>"
    sequence_id: int     # 676 (the integer behind the human form)
    title: str
    description: str
    state_id: str        # UUID of the state — names require a separate lookup
    project_id: str      # UUID
    workspace_slug: str  # "xair-org-ai"
    state_name: str = ""        # "Backlog" / "In Progress" / etc. — resolved from state_id
    team_key: str = "<tracker-prefix>"     # Plane project identifier (constant for the configured tracker project)
    git_branch_name: str = ""   # Generated: "bernarduriza/visal-<seq>-<slug>"
    project_name: str = "xair-org"
    assignee_name: str = ""
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)


# ── Provider-agnostic Issue type (used by pipelines after Plane migration) ──

# Type alias: any pipeline that accepts an issue should annotate as `Issue`
# rather than the concrete provider type. Both TrackerIssue and PlaneIssue
# expose the same field surface (id, identifier, title, description,
# state_name, team_key, git_branch_name, project_name, assignee_name, labels)
# so duck-typing works at runtime.
Issue = TrackerIssue | PlaneIssue

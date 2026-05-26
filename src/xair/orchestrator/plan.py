"""Pydantic schema for a VairPlan — the DAG output of the orchestrator planner.

A VairPlan is the planner's contract with the executor. Each step is a single
invocation of a granular XAIR command (ai-resolve, ai-review, ai-remedy).
Steps form a DAG via ``depends_on`` — steps with no unmet deps run in
parallel, others wait.

Multi-repo (PoC v3+): the plan binds to ONE umbrella Issue (``issue`` +
``issue_repo``) that typically lives in ``xair-org/.github``, but each step
declares its own target ``repo``. Steps in the same plan can land PRs across
multiple repositories.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


_REPO_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class StepType(str, Enum):
    """Which granular XAIR command an executor should dispatch for this step."""

    AI_RESOLVE = "ai-resolve"
    AI_REVIEW = "ai-review"
    AI_REMEDY = "ai-remedy"


class VairStep(BaseModel):
    """One node in the orchestration DAG."""

    id: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Stable identifier within the plan (e.g. 's1', 'extract-sse-writer').",
    )
    type: StepType = Field(..., description="Which XAIR command to dispatch.")
    repo: str | None = Field(
        default=None,
        pattern=_REPO_PATTERN,
        description=(
            "owner/repo where this step's PR lands. When None, falls back to "
            "VairPlan.default_repo (legacy single-repo plans). Required for "
            "multi-repo plans; the validator promotes the plan-level default "
            "into each step that omits it."
        ),
    )
    spec: str = Field(
        ...,
        min_length=20,
        max_length=4000,
        description=(
            "Human-readable instruction passed to the sub-agent. For ai-resolve "
            "this is the body of the synthetic Issue the granular resolver runs against. "
            "For ai-review/ai-remedy this is the comment body posted on the target PR."
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Step ids that must complete before this step starts.",
    )
    target_pr_from: str | None = Field(
        default=None,
        description=(
            "For ai-review/ai-remedy steps: the id of the upstream step whose "
            "PR this step targets. Resolved at execution time to the actual PR number."
        ),
    )

    @model_validator(mode="after")
    def _review_steps_must_target_a_pr(self) -> "VairStep":
        if self.type in (StepType.AI_REVIEW, StepType.AI_REMEDY):
            if not self.target_pr_from:
                raise ValueError(
                    f"Step {self.id!r} is type {self.type.value} but has no "
                    f"target_pr_from — review/remedy must reference an upstream "
                    f"ai-resolve step."
                )
            # Auto-derive dependency: if target_pr_from is set, it MUST be a
            # prerequisite by definition (you can't review a PR that doesn't
            # exist yet). Add it to depends_on silently rather than failing
            # on what the model can derive.
            if self.target_pr_from not in self.depends_on:
                object.__setattr__(
                    self, "depends_on", [*self.depends_on, self.target_pr_from]
                )
        return self


class VairPlan(BaseModel):
    """The orchestrator's output: an executable DAG bound to one umbrella issue.

    A plan binds to ONE umbrella Issue (``issue`` + ``issue_repo``) that may
    live in a different repository from the steps it executes against —
    by convention ``xair-org/.github`` for cluster-wide work. Each step
    declares its own target ``repo``; steps in the same plan can land PRs
    across multiple repositories.

    Backward compatibility: the historical plan-level ``repo`` field is
    accepted as an alias for ``default_repo`` via a ``mode="before"``
    validator. ``default_repo`` fills in for any step that omits ``repo``.
    Existing single-repo plans continue to validate unchanged.
    """

    issue: int = Field(..., gt=0, description="The umbrella Issue number this plan resolves.")
    issue_repo: str = Field(
        default="xair-org/.github",
        pattern=_REPO_PATTERN,
        description=(
            "owner/repo where the umbrella Issue lives. Defaults to "
            "xair-org/.github so cluster issues for cross-repo work have a "
            "canonical home. Decoupled from per-step target repos."
        ),
    )
    default_repo: str | None = Field(
        default=None,
        pattern=_REPO_PATTERN,
        description=(
            "Fallback target repo for steps that omit their own ``repo``. "
            "Equivalent to the legacy plan-level ``repo`` field. When None, "
            "every step MUST declare its own repo."
        ),
    )
    summary: str = Field(
        ...,
        min_length=20,
        max_length=1500,
        description="One-paragraph plan summary for the human reviewer.",
    )
    steps: list[VairStep] = Field(..., min_length=1, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_repo_alias(cls, data: object) -> object:
        """Accept the historical ``repo`` field as an alias for ``default_repo``.

        Plans posted before the multi-repo schema landed used ``repo`` at the
        plan level. The planner emits ``default_repo`` going forward, but we
        keep the alias so re-runs of older plan comments don't break.
        """
        if not isinstance(data, dict):
            return data
        if "repo" in data and "default_repo" not in data:
            data["default_repo"] = data.pop("repo")
        return data

    @model_validator(mode="after")
    def _resolve_step_repos(self) -> "VairPlan":
        """Fill missing ``step.repo`` from ``default_repo`` or fail loudly."""
        for step in self.steps:
            if step.repo is None:
                if self.default_repo is None:
                    raise ValueError(
                        f"Step {step.id!r} has no ``repo`` and the plan has no "
                        f"``default_repo`` to fall back on. Multi-repo plans must "
                        f"declare ``repo`` on every step."
                    )
                object.__setattr__(step, "repo", self.default_repo)
        return self

    @property
    def target_repos(self) -> set[str]:
        """All distinct target repos this plan touches. Useful for pre-flight checkouts."""
        return {step.repo for step in self.steps if step.repo is not None}

    @model_validator(mode="after")
    def _validate_dag(self) -> "VairPlan":
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("Duplicate step ids in plan.")

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"Step {step.id!r} depends_on {dep!r} which is not in the plan."
                    )
                if dep == step.id:
                    raise ValueError(f"Step {step.id!r} depends on itself.")

        # Cycle detection via DFS with three-color marking.
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {s.id: WHITE for s in self.steps}
        deps: dict[str, list[str]] = {s.id: s.depends_on for s in self.steps}

        def visit(node: str, stack: list[str]) -> None:
            if color[node] == GRAY:
                cycle = " → ".join(stack[stack.index(node):] + [node])
                raise ValueError(f"Cycle detected in plan DAG: {cycle}")
            if color[node] == BLACK:
                return
            color[node] = GRAY
            stack.append(node)
            for d in deps[node]:
                visit(d, stack)
            stack.pop()
            color[node] = BLACK

        for node in deps:
            if color[node] == WHITE:
                visit(node, [])

        return self

    def topological_waves(self) -> list[list[VairStep]]:
        """Return steps grouped into parallel-executable waves.

        Wave N contains all steps whose deps are entirely in waves 0..N-1.
        Executor runs each wave in parallel, waits for completion, then advances.
        """
        remaining = {s.id: set(s.depends_on) for s in self.steps}
        by_id = {s.id: s for s in self.steps}
        waves: list[list[VairStep]] = []
        completed: set[str] = set()

        while remaining:
            ready = [sid for sid, deps in remaining.items() if deps <= completed]
            if not ready:
                # Should be unreachable — _validate_dag would have caught it.
                raise RuntimeError("Stuck in toposort — plan validation should have caught this.")
            waves.append([by_id[sid] for sid in ready])
            for sid in ready:
                del remaining[sid]
                completed.add(sid)

        return waves

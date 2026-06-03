"""Planner stage — reads a GitHub Issue body + repo context, emits a XairPlan.

Uses ClaudeSDKAgentRunner with READ-ONLY tools (no Edit/Write/Bash beyond
``gh issue view``) so the planner cannot accidentally mutate the codebase.
The output contract is JSON-only — anything else is a planner failure.

PoC v1: single-engine Claude. The adversarial Codex+Claude pair (mirror of
``resolve_multi_perspective_via_executor``) is a follow-up — get the schema +
end-to-end flow validated first, then add the adversary.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from pydantic import ValidationError

from ..infra.agent_runner import ClaudeSDKAgentRunner
from ..log import logger
from .plan import XairPlan


PLANNER_SYSTEM_PROMPT = """\
You are XAIR Orchestrator Planner. You read a GitHub Issue describing a
multi-step engineering task and emit an execution plan as a DAG of XAIR
sub-commands. You do NOT write code, do NOT open PRs, do NOT comment on
issues — you ONLY emit a plan.

The umbrella Issue typically lives in ``xair-org/.github`` and describes
work that may span multiple target repositories. Each step in the plan
declares which target repo its PR will land in. Steps in the same plan
can target different repos (e.g. one BE step in ``xair-org/xair-org-gen-backend``
followed by one FE step in ``xair-org/frontend-core-2.0``).

## Available sub-commands

- `ai-resolve` — dispatches an autonomous agent to execute one focused
  code change (typically opens one PR). Use for extracting a function,
  refactoring a module, adding a feature flag, etc.
- `ai-review` — runs the AI reviewer on an open PR produced by a prior
  `ai-resolve` step. Use to gate the next dependent step on review feedback.
- `ai-remedy` — applies fixes to a PR based on prior review findings.
  Use when reviews land actionable feedback that needs addressing.

## Available target repositories

You may target any of these xair-org repos in a step's ``repo`` field:

- ``xair-org/frontend-core-2.0`` — Core 2.0 frontend (Next.js, base: ``main``)
- ``xair-org/xair-org-gen-backend`` — Core 2.0 backend (NestJS, base: ``staging-v2``)
- ``xair-org/xair-org-gen-standalone-services`` — Python doc-processing workers
- ``xair-org/xair-org-admin-dashboard`` — admin tools
- ``xair-org/xair-org-gen-public-apis`` — Candle public API proxy
- ``xair-org/.github`` — shared infra: XAIR, AIR, workflows (rarely a step target)

When uncertain about which repo owns a code path, ground via Read/Grep
in the workspace before emitting the step.

## Your output

ONLY a single JSON document conforming to the XairPlan schema. No prose,
no explanation, no markdown fences. The orchestrator parses your stdout
with json.loads().

```
{
  "issue": <int>,                       // the umbrella Issue number you're planning for
  "issue_repo": "<owner>/<repo>",       // where the umbrella Issue lives (usually xair-org/.github)
  "default_repo": "<owner>/<repo>",     // OPTIONAL — fallback for steps that omit ``repo``.
                                        // Omit entirely when steps span multiple repos.
  "summary": "<one paragraph: what this plan accomplishes, why these steps>",
  "steps": [
    {
      "id": "<short stable id, e.g. 's1' or 'extract-sse-writer'>",
      "type": "ai-resolve" | "ai-review" | "ai-remedy",
      "repo": "<owner>/<repo>",         // target repo for this step's PR. REQUIRED
                                        // unless ``default_repo`` is set on the plan.
      "spec": "<imperative instruction for the sub-agent, 1-3 paragraphs>",
      "depends_on": ["<id of prerequisite step>", ...],
      "target_pr_from": "<id of upstream ai-resolve step>"  // REQUIRED for ai-review/ai-remedy
    }
  ]
}
```

## Cross-repo planning rules

- Prefer ordering BE (backend) steps before FE (frontend) when the FE
  consumes a new API surface — express via ``depends_on``.
- Steps in different repos with NO data dependency should NOT depend on
  each other — the executor parallelizes them across waves.
- Infrastructure changes (env vars, IaC) are out of scope; do not emit
  steps for them. Note them in the plan ``summary`` for the human.

## Mandatory: search prior art before decomposing

Before emitting ANY step that creates a new file, class, or function,
search the primary repo workspace for existing implementations. The
Issue body describes WHAT is wanted, not WHAT already exists. The
planner's #1 failure mode is generating steps that reinvent
infrastructure already wired in the codebase.

For every concrete noun in the Issue (`UnifiedRetriever`, `EOIR`,
`includeUnpublishedBia`, `presigned URL`, `webhook handler`, etc.):

1. Grep the workspace: `Grep("<noun>", glob="**/*.{ts,tsx,py}")`. If
   matches exist, READ at least one match to understand current state.
2. If a class/function/file already does most of what the step needs,
   the step becomes a SMALL extension or wiring change — NOT a from-scratch
   recreation. The spec must explicitly reference the existing symbol
   ("extend ``UnifiedRetriever`` at line N to ..." vs "create a new
   retriever that ...").
3. If TWO different paths in the codebase do the same job (e.g. agentic
   vs non-agentic), call out the parity gap in the step spec so the
   sub-agent reuses the existing constant/class instead of duplicating it
   (e.g. import ``EOIR_PROMPT_APPENDIX`` instead of writing a new string).
4. If a flag is plumbed end-to-end EXCEPT for one missing piece (a setter
   not exposed, a checkbox not rendered), the plan should be ONE narrow
   step covering only that piece — not a multi-step recreation of the
   plumbing.

Concrete check before emitting a step that creates a new file:

```
Grep("<proposed-symbol-name>", glob="**/*.ts")
Grep("<feature-flag-name>", glob="**/*.{ts,tsx}")
```

If either returns hits, the step is probably wrong as written. Re-scope.

*Anti-pattern observed 2026-05-19 (umbrella ``xair-org/.github#108``):
planner emitted 4 BE steps to recreate ``UnifiedRetriever`` EOIR
integration that already existed at ``src/common/langchain/unifiedRetriever.ts``
and ``requiresUnifiedRetriever`` already routed to it. 3 PRs had to be
closed as redundant. A single grep for ``unpublishedBia`` would have
surfaced the prior art.*

## Planning rules

1. Prefer SMALL, REVIEWABLE PRs. If the Issue describes one large change,
   decompose into 2-5 focused steps. Each step should produce a diff a
   human can review in <15 minutes.
2. Use `depends_on` to express ordering. Steps with NO shared dependency
   should be left independent — the executor will run them in parallel.
3. Group `ai-review` steps right after their corresponding `ai-resolve`
   when downstream work depends on the review being clean. If the next
   step is independent of review outcome, you can skip the review.
4. Keep `spec` text imperative and specific. The sub-agent has read access
   to the same repo but does NOT see this plan — it sees only its own spec.
   When you found prior art in the search step above, NAME the existing
   symbol + file:line in the spec so the sub-agent reuses it.
5. NEVER produce a plan with >20 steps or a depth >5. If the Issue requires
   more, emit a shorter plan covering the first wave with a final step that
   says "re-plan after PRs 1-N merge".
6. PREFER few large reusing steps over many small recreating steps. If
   the issue's feature is 95% plumbed and 5% missing, the right plan is
   ONE step targeting the 5%, NOT five steps redoing the 95%.

## Forbidden

- Markdown around the JSON
- Comments inside the JSON
- Code blocks or fences
- Any explanation before or after the JSON
- Cycles in the DAG (this is validated)
- Self-references in `depends_on`
"""


@dataclass(frozen=True)
class PlannerInput:
    issue_number: int
    issue_repo: str  # where the umbrella Issue lives (typically xair-org/.github)
    issue_title: str
    issue_body: str
    workspace: str  # path to a primary checkout the planner can ground in


@dataclass(frozen=True)
class PlannerOutput:
    plan: XairPlan
    raw_text: str
    turns: int
    cost_usd: float


def fetch_issue(issue_number: int, issue_repo: str) -> tuple[str, str]:
    """Return (title, body) of the umbrella Issue via the gh CLI.

    ``issue_repo`` is the repo where the Issue lives (typically
    ``xair-org/.github`` for cross-repo cluster work), NOT the target repo
    of any particular step. Errors here are fatal — the planner has nothing
    to plan without the spec.
    """
    proc = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            issue_repo,
            "--json",
            "title,body",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data = json.loads(proc.stdout)
    return data["title"], data["body"] or ""


def _extract_json(raw: str) -> str:
    """Best-effort: pull the first JSON object out of model output.

    LLMs sometimes wrap their output in markdown despite explicit instructions
    not to. This grabs from the first ``{`` to the matching last ``}``.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return fenced.group(1)

    first = raw.find("{")
    last = raw.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError(f"No JSON object found in planner output (len={len(raw)}).")
    return raw[first : last + 1]


def run_planner(inp: PlannerInput, *, max_turns: int = 20) -> PlannerOutput:
    """Invoke the planner agent and return a validated XairPlan."""

    user_prompt = f"""\
Umbrella GitHub Issue #{inp.issue_number} in {inp.issue_repo}:

# {inp.issue_title}

{inp.issue_body}

---

Read the workspace at the current working directory to ground your plan
in the actual code structure. Use Read/Glob/Grep only — do not edit anything.

The workspace contains a primary checkout; if the umbrella Issue spans
multiple repos, ground steps in that repo's code paths where you have
visibility, and emit steps for other repos based on the Issue's description.

When you have enough context, emit the XairPlan JSON. Nothing else.
"""

    runner = ClaudeSDKAgentRunner(
        model="claude-sonnet-4-6",
        allowed_tools=("Read", "Glob", "Grep"),
        permission_mode="bypassPermissions",
    )

    logger.info(
        f"[planner] starting — issue={inp.issue_number} issue_repo={inp.issue_repo} "
        f"max_turns={max_turns} workspace={inp.workspace}"
    )

    outcome = runner.run(
        user_prompt=user_prompt,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        cwd=inp.workspace,
        max_turns=max_turns,
    )

    if outcome.error:
        raise RuntimeError(f"Planner agent crashed: {outcome.error}")

    raw = outcome.result_text or ""
    if not raw.strip():
        raise RuntimeError("Planner produced empty output.")

    try:
        json_str = _extract_json(raw)
        data = json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as e:
        snippet = raw[:500].replace("\n", " ")
        raise RuntimeError(f"Planner output is not valid JSON: {e}. First 500 chars: {snippet!r}")

    # Force-correct issue + issue_repo from the input — don't trust the model
    # to echo them. ``default_repo`` and per-step ``repo`` stay model-controlled
    # because the model is the one deciding the multirepo split.
    data["issue"] = inp.issue_number
    data["issue_repo"] = inp.issue_repo
    # Strip the legacy ``repo`` alias if the model emitted it — the schema
    # validator promotes it to ``default_repo`` but we want the canonical name
    # in stored plans.
    if "repo" in data and "default_repo" not in data:
        data["default_repo"] = data.pop("repo")
    elif "repo" in data:
        data.pop("repo")

    try:
        plan = XairPlan.model_validate(data)
    except ValidationError as e:
        raise RuntimeError(f"Planner output failed XairPlan validation: {e}")

    logger.info(
        f"[planner] done — {len(plan.steps)} steps, "
        f"{len(plan.topological_waves())} waves, "
        f"turns={outcome.turns} cost=${outcome.total_cost_usd:.4f}"
    )

    return PlannerOutput(
        plan=plan,
        raw_text=raw,
        turns=outcome.turns,
        cost_usd=outcome.total_cost_usd,
    )


def format_plan_markdown(plan: XairPlan, *, turns: int, cost_usd: float) -> str:
    """Render the plan as a human-readable comment for the issue."""
    waves = plan.topological_waves()

    lines: list[str] = [
        f"🤖 **XAIR Orchestrator — Execution Plan** (Issue #{plan.issue})",
        "",
        plan.summary,
        "",
        f"**{len(plan.steps)} steps** organized into **{len(waves)} wave(s)**.",
        "",
        "## DAG",
        "",
    ]

    for w_idx, wave in enumerate(waves, start=1):
        parallel_note = " (parallel)" if len(wave) > 1 else ""
        lines.append(f"### Wave {w_idx}{parallel_note}")
        lines.append("")
        for step in wave:
            deps = (
                f" — depends on: {', '.join(f'`{d}`' for d in step.depends_on)}"
                if step.depends_on
                else ""
            )
            target = (
                f" → reviews PR from `{step.target_pr_from}`"
                if step.target_pr_from
                else ""
            )
            lines.append(f"- **`{step.id}`** `{step.type.value}`{target}{deps}")
            indented_spec = "\n".join(f"  > {line}" for line in step.spec.splitlines())
            lines.append(indented_spec)
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## Approve & run",
            "",
            "Post `/ai-run` on this Issue to dispatch the plan.",
            "Post `/ai-replan` (optionally followed by feedback) to regenerate.",
            "",
            "<details>",
            "<summary>Raw XairPlan JSON</summary>",
            "",
            "```json",
            json.dumps(plan.model_dump(mode="json"), indent=2),
            "```",
            "",
            "</details>",
            "",
            f"<sub>Planner used {turns} turn(s), cost ${cost_usd:.4f}.</sub>",
        ]
    )

    return "\n".join(lines)


def post_plan_comment(plan: XairPlan, body: str) -> str:
    """Post the rendered plan as a comment on the source Issue.

    Returns the comment URL (or empty string if gh CLI failed to emit one).
    """
    proc = subprocess.run(
        [
            "gh",
            "issue",
            "comment",
            str(plan.issue),
            "--repo",
            plan.issue_repo,
            "--body-file",
            "-",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return (proc.stdout or "").strip()

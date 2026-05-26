"""XAIR Orchestrator — meta-agent that plans DAGs of XAIR sub-commands.

The orchestrator reads a high-level GitHub Issue (labeled ``ai-ready``),
emits an LLM-generated execution plan as a typed ``VairPlan``, and posts
the plan back as a comment for human approval. Once approved (via
``/ai-run``), a separate executor traverses the DAG dispatching the
granular XAIR commands (``/ai-resolve`` per node, ``/ai-review`` when
deps require it).

PoC scope (this module today):
    - Pydantic ``VairPlan`` schema with topological validation
    - ``planner`` stage: Claude reads Issue body + repo context → VairPlan
    - Comment poster: serializes plan to markdown + posts via gh CLI

Out of PoC scope (follow-up):
    - Adversarial Codex+Claude planner pair (single-engine for now)
    - Executor that dispatches workflows in topological order
    - ``/ai-run`` and ``/ai-replan`` comment handling
    - Throttling layer
"""

from .executor import ExecutionResult, StepResult, execute_plan
from .plan import StepType, VairPlan, VairStep
from .plan_loader import PlanNotFoundError, load_latest_plan

__all__ = [
    "ExecutionResult",
    "PlanNotFoundError",
    "StepResult",
    "StepType",
    "VairPlan",
    "VairStep",
    "execute_plan",
    "load_latest_plan",
]

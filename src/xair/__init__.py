"""xair — the X-AI-Reviewer framework.

A generic, engine-agnostic Python toolkit for building LLM-powered CLI
commands wired to GitHub Actions workflows. Each command is a pipeline:

    gather → format → LLM → emit

xair ships the framework plumbing (dispatch, command registry, contracts,
gatherers, providers, orchestration, policies). Concrete pipelines and
deployment-specific gatherers live in CONSUMER packages — see
``bair`` for one such consumer.

Public API
----------

    from xair.dispatch import dispatch
    from xair.command_registry import (
        command,
        register_ack_meta,
        CommandContext,
        DispatchResult,
    )

    @command("explain")
    def handle_explain(ctx, container) -> None:
        ...

To run::

    python -m xair explain        # framework dispatcher
    # (or expose via your consumer's CLI; see the bair example.)

What lives where
----------------

``xair.dispatch``           generic registry-driven dispatcher
``xair.command_registry``   @command decorator + CommandContext + parse_comment
``xair.contracts``          Protocols (GitHub, IssueTracker, Provider, …)
``xair.infra``              concrete providers (OpenAI, GitHub, Slack, …)
``xair.gatherers``          PR diff / commits / issues / threads / CI status
``xair.prompt``             base prompt builders
``xair.domain``             models, plan, narrator, verdict, invariants
``xair.orchestration``      pipeline + stage executor + tracing
``xair.orchestrator``       multi-step DAG planner
``xair.policies``           budget, escalation, gating, routing, security
``xair.testing``            fakes for unit tests
``xair.log``                loguru configuration (CI + local)

Engine-agnostic framework, generalized for public release.
"""

__version__ = "0.0.1"

# ``import xair`` is deliberately CHEAP: this package's __init__ only sets
# __version__. Submodules (dispatch, command_registry, infra, contracts,
# gatherers, orchestration, …) load on demand via Python's normal import
# system when the consumer writes ``from xair.dispatch import dispatch``
# or ``import xair.infra``. This avoids pulling loguru, pydantic, httpx
# (and on-demand the openai / anthropic SDKs) at package-load time for
# callers that just want ``xair.__version__``.
__all__ = ["__version__"]

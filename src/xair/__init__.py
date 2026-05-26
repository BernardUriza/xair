"""xair — the X-AI-Reviewer framework.

A generic, engine-agnostic Python toolkit for building LLM-powered CLI
commands wired to GitHub Actions workflows. Each command is a pipeline:

    gather → format → LLM → emit

xair ships the framework plumbing (dispatch, command registry, contracts,
gatherers, providers, orchestration, policies). Concrete pipelines and
deployment-specific gatherers live in CONSUMER packages — see
``bair`` for one such consumer.

Public API:

    from xair.dispatch import dispatch
    from xair.command_registry import command

    @command("explain")
    def run() -> int:
        ...

Originally extracted from the Visalaw VAIR prototype (2026-05-26).
"""

__version__ = "0.0.1"

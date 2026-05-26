# xair

**X-AI-Reviewer** — engine-agnostic framework for building LLM-powered CLI commands wired to GitHub Actions workflows. Every command is a pipeline:

```
gather → format → LLM → emit
```

Install via pip OR conda; consumers register commands via decorator and call `python -m xair <command>` from CI or locally.

## Why xair exists

Extracted from the production Visalaw VAIR prototype (2026-05-26). The framework was deliberately separated from any one company's pipelines so it could be reused by other AI-engineering teams without inheriting Visalaw-specific gatherers (Plane, Mongo, CloudWatch). The first concrete consumer is [bair](https://github.com/BernardUriza/.github/tree/main/bair).

## Install

### pip

```bash
pip install git+https://github.com/BernardUriza/xair@main
# or once on PyPI:
pip install xair
```

### conda (local build for now)

```bash
git clone https://github.com/BernardUriza/xair
cd xair
conda install -n base -c conda-forge conda-build
conda build conda-recipe/ -c conda-forge
conda install -c local xair
```

The recipe is `noarch: python` — the same build artifact works on linux/macOS/win.

## The X-AIR pattern in 6 steps

Create a new XAIR command (e.g. `/ai-explain`):

1. **Write the pipeline** at `<your-consumer-pkg>/pipelines/explain.py`:
   ```python
   from xair.command_registry import command

   @command("explain")
   def run() -> int:
       ctx = gather()
       prompt = build_prompt(ctx)
       result = llm.complete(prompt, engine="gpt")
       emit(result)
       return 0
   ```
2. **Write the prompt builder** under `<your-consumer-pkg>/prompts/`.
3. **Add the gatherer** (or reuse `xair.gatherers.{diff, commits, issues, threads, ...}`).
4. **Write the reusable workflow** at `.github/workflows/ai-explain.yml` calling `python -m <consumer-pkg> explain`.
5. **Wire the caller** in each target repo's `.github/workflows/ai-commands.yml` as a `uses:` passthrough.
6. **Test**: `gh workflow run ai-explain.yml --repo <target>`.

## Layout

```
src/xair/
├── __init__.py         version
├── __main__.py         python -m xair <cmd>
├── dispatch.py         command dispatcher
├── command_registry.py @command decorator
├── log.py              loguru config (CI + local)
├── contracts/          Protocols (GitHub, IssueTracker, Provider, Publisher, …)
├── infra/              concrete providers (OpenAI, GitHub, Slack, GraphQL, …)
├── gatherers/          GH PR/diff/issues/CI/commits/threads readers
├── prompt/             base prompt builders (claude/agent_summary/trace)
├── domain/             models, plan, narrator, verdict, invariants
├── orchestration/      pipeline + stage executor + tracing
├── orchestrator/       multi-step DAG planner + executor
├── policies/           budget, escalation, gating, routing, security
├── services/           local_runner
├── testing/            fakes for tests
└── queries/            *.graphql (GitHub API)

conda-recipe/
├── meta.yaml           conda-build recipe (noarch: python)
└── README.md           how to build/install
```

## What's deliberately NOT in xair

- **Plane / Linear / Jira integrations** — wire your own `IssueTrackerClient` via `Container` subclass.
- **Slack channel names, GitHub App identities, secret names** — that's deployment config (lives in your consumer).
- **Concrete pipelines** (`review`, `remedy`, `retro`, `preflight`, `changelog`, `issue_rank`, `resolve`, `revert`) — examples available in [bair](https://github.com/BernardUriza/.github/tree/main/bair/src/bair/pipelines).
- **AIR queue runtime** — separate decommissioned subsystem from the original prototype; not ported.
- **Frontend dashboard** — lives in [bair](https://github.com/BernardUriza/.github/tree/main/bair/frontend).

## License

MIT. Originally adapted from the Visalaw VAIR / XAIR reference implementation, 2026.

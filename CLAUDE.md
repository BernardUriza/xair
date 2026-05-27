# xair · Quick Reference

**X-AI-Reviewer** — engine-agnostic framework for building LLM-powered CLI commands wired to GitHub Actions workflows. Every command is a pipeline: `gather → format → LLM → emit`.

**Owner:** Bernard Uriza Orozco
**Status:** v0.0.1 (extracted from production Visalaw VAIR prototype, 2026-05-26)
**Repo:** https://github.com/BernardUriza/xair
**First consumer:** [bair](https://github.com/BernardUriza/.github/tree/main/bair)

---

## 🚀 Quick Start

```bash
# Install (editable, for local dev)
pip install -e .

# Install with OpenAI extra (Container.production needs it for now)
pip install -e ".[openai]"

# Conda build (noarch:python; one artifact works linux/macOS/win)
conda build conda-recipe/
```

---

## 📚 Layout

```
src/xair/
├── __init__.py
├── __main__.py            # python -m xair <cmd> (thin stub)
├── dispatch.py            # registry-driven dispatcher (110 LOC)
├── command_registry.py    # @command decorator + CommandContext (207 LOC)
├── log.py                 # loguru CI/local config
├── contracts/             # Protocols: GitHub, IssueTracker, Provider, …
├── infra/                 # concrete providers: OpenAI, GitHub, Slack, GraphQL
├── gatherers/             # diff, commits, ci_status, issues, threads, …
├── prompt/                # base prompt builders
├── domain/                # models, plan, narrator, verdict
├── orchestration/         # pipeline + stage executor + tracing
├── orchestrator/          # multi-step DAG planner
├── policies/              # budget, escalation, gating, routing, security
├── services/              # framework services (currently empty)
├── testing/               # fakes for tests
└── queries/               # *.graphql (GitHub API)

conda-recipe/              # conda-build noarch recipe
```

---

## 🎯 Core Principles

1. **Framework-only.** xair must NEVER import consumer-specific code. → [rules/framework-genericity.md](.claude/rules/framework-genericity.md)
2. **Registry over hardcoded routing.** Commands self-register via `@command(...)`; dispatch never knows specific command names. → [rules/registry-pattern.md](.claude/rules/registry-pattern.md)
3. **Pre-1.0, no backwards-compat shims.** Breaking changes are fine; bump the version. → [rules/breaking-changes.md](.claude/rules/breaking-changes.md)
4. **Optional deps are LAZY-imported.** Module-level `from <optional> import …` breaks consumers who don't install the extra.

---

## 🏗️ Adding a New Concept

Adding a new **command** is consumer-side (see bair). The xair framework only changes when:
- A new gatherer covers a NEW data source available to ALL consumers (e.g., a generic CI gatherer for GitLab CI, not Visalaw's CloudWatch)
- A new Protocol contract is needed (add to `contracts/`)
- A new infra provider is needed (add to `infra/`, lazy-import its SDK)

**Before adding anything to xair, ask:** can the consumer (bair, vair, future XAIRs) implement this themselves? If yes, it does not belong in xair.

---

## 🚫 Critical Rules

### No consumer imports
```python
# ❌ NEVER in xair
from ..config.review import DeepMode

# ✅ Consumer passes its types via Protocol
from ..contracts import Config
def gather(cfg: Config) -> ...:
```

### Lazy-load optional SDK deps
```python
# ❌ Breaks consumers without openai SDK
from openai import OpenAI  # module-level

# ✅ Pulls only when actually instantiated
def __init__(self):
    from openai import OpenAI
    self.client = OpenAI()
```

### Conventional commits
`feat:` `fix:` `refactor:` `chore:` `docs:` `test:` — same as free-intelligence.

---

## 🔗 Related

- [BAIR](https://github.com/BernardUriza/.github/tree/main/bair) — Bernard's instance of the X-AIR pattern
- [free-intelligence](https://github.com/BernardUriza/free-intelligence) — first repo using BAIR Gatekeeper via App-token integration
- Origin: production [Visalaw VAIR](https://github.com/Visalaw/.github) prototype (2026-05-26)

---

**License:** MIT. Reuse and adapt freely; cite as Visalaw — VAIR / AIR / XAIR Reference Implementation, 2026.

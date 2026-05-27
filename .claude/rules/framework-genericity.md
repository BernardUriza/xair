# Framework Genericity

xair is the FRAMEWORK layer. It must never import or depend on any consumer-specific concept.

## The Hard Rule

A module under `src/xair/` cannot have either of the following at module load time:

- `from ..config.<X> import …` — `config/` is consumer territory (lives in bair, vair, etc.)
- `from ..pipelines.<X> import …` — concrete pipelines are consumer territory
- `from <optional-sdk> import …` at module top level — breaks consumers that don't install that extra

If a file under `src/xair/` violates either of the first two, it is consumer code in disguise and belongs in the consumer's package (`bair/src/bair/<dir>/`), not in xair.

## Why It Matters

The first xair extraction had two files that broke this rule:

- `xair/gatherers/commits.py` depended on `bair.config.ChangelogConfig` — moved out.
- `xair/services/local_runner.py` depended on `bair.config.review.DeepMode` plus had consumer-specific functions `run_local_review`, `run_local_retro` — moved out.

A third file (`xair/command_registry.py`) imported `DeepMode` even though it claimed to be framework code. That coupling forced every consumer to install `bair.config.review` even if they wrote their own pipelines. Eliminated in the v0.0.1 refactor by replacing `CommandContext.deep_mode: DeepMode` with a generic `options: dict[str, str]`.

## How to Validate Before Commit

```bash
# From repo root
grep -rln "from \.\.config\|from \.\.pipelines" src/xair/ && echo "VIOLATION" || echo "clean"
```

Should print `clean`. If it prints any file, that file either:

1. Moves to a consumer package, OR
2. Gets refactored to accept a Protocol parameter instead of importing the concrete type.

## Lazy-Loading Optional SDK Dependencies

xair's pyproject declares several optional extras (`openai`, future ones for `anthropic`, etc.). Modules that wrap an optional SDK MUST import lazily:

```python
# ❌ openai SDK becomes a HARD requirement at xair load
from openai import OpenAI, APIError

class OpenAIProvider:
    def __init__(self):
        self.client = OpenAI()
```

```python
# ✅ SDK only loaded when the provider is actually instantiated
class OpenAIProvider:
    def __init__(self):
        from openai import OpenAI  # noqa: PLC0415  — lazy on purpose
        self.client = OpenAI()
```

`Container.production()` follows the same pattern: it lazy-imports `OpenAIProvider` and falls back to a `_StubLlm` if `OPENAI_API_KEY` is not set, so consumers that use a different LLM provider (BAIR Gatekeeper talks to Anthropic via httpx) build their Container without ever pulling the openai SDK at construction time.

## What "Consumer-Specific" Means

When in doubt, run this check: would another XAIR consumer with NO knowledge of Visalaw/Bernard/free-intelligence need this code? If no → it belongs in that one consumer, not in xair.

Examples:

| Code | Where |
|------|-------|
| Generic GitHub PR-diff gatherer | xair (`gatherers/diff.py`) |
| Visalaw's CloudWatch metrics gatherer | their consumer (`vair/gatherers/cloudwatch.py`) |
| Bernard's BAIR Gatekeeper pipeline | bair (`bair/pipelines/gatekeep.py`) |
| Container's protocol for an issue tracker | xair (`contracts/issue_tracker.py`) |
| Plane API client implementation | a future plugin or `bair/infra/plane_provider.py` |

# Registry Pattern

Commands self-register via `@command(...)`. The dispatcher routes by name without knowing any specific command at compile time.

## The Hard Rule

`xair/dispatch.py` must NEVER:

- Hold a hardcoded set like `_COMMANDS = {"review", "retro", ...}`
- Import any concrete pipeline (`from ..pipelines.review import …`)
- Branch on `if command == "<specific-name>"`

If you find yourself wanting to do any of the above, the logic belongs in the consumer's pipeline handler.

## Why It Matters

The original dispatcher carried ~434 LOC of consumer-specific routing:

```python
_COMMANDS = {"review", "retro", "remedy", "revert"}        # hardcoded
_CLAUDE_ONLY_COMMANDS = {"remedy"}                          # consumer policy
_NO_ENGINE_COMMANDS = {"revert"}                            # consumer policy
…
from ..config.review import DeepMode, ReviewConfig          # consumer import
from ..pipelines.review_via_executor import run_…           # consumer import
```

The `@command` decorator existed but the dispatcher never read `_REGISTRY` — it routed by string. The "registry" was dead code. The v0.0.1 refactor cut dispatch.py to ~110 LOC by deleting all hardcoded routing and making the dispatcher a thin wrapper around `command_registry.get_handler(name)`.

## How Commands Register

Consumer side:

```python
# bair/src/bair/pipelines/gatekeep.py
from xair.command_registry import command, CommandContext
from xair.infra.container import Container

@command("gatekeep")
def gatekeep(ctx: CommandContext, container: Container) -> None:
    ...
```

Side-effect import in `bair/src/bair/pipelines/__init__.py`:

```python
from . import gatekeep  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

When `python -m bair gatekeep` runs:

1. `bair.__main__` imports `bair.pipelines` (triggers the side-effect)
2. `gatekeep` module loads, the `@command("gatekeep")` decorator runs, registers the handler in xair's `_REGISTRY`
3. Then `from xair.dispatch import dispatch; dispatch(sys.argv[1:])` looks up `"gatekeep"` in `_REGISTRY` and runs it

## Adding a New Command (Consumer-side)

1. Write `<consumer>/pipelines/<name>.py` with the handler decorated `@command("<name>")`
2. Append `from . import <name>  # noqa: F401` to `<consumer>/pipelines/__init__.py`
3. Write a workflow that runs `python -m <consumer> <name>` with env vars the handler needs
4. Done. xair never changes.

## Generic Option Parsing

`CommandContext.options: dict[str, str]` carries free-form `key:value` tokens from the trigger comment. Consumers map them into their own typed configs:

```python
# In a consumer pipeline
def gatekeep(ctx: CommandContext, container: Container) -> None:
    deep_mode = ctx.options.get("deep") == "true"
    threshold = float(ctx.options.get("threshold", "0.5"))
    ...
```

xair's parser also extracts:

- `engine: str` (gpt | claude | none) — universal LLM selector
- Bare flags (`deep`, `nodeep`, `dryrun`) appear in options with value `"true"`

If a consumer needs typed parsing, do it INSIDE the handler. Don't ask xair to grow a generic schema.

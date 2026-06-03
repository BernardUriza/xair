# Breaking Changes (Pre-1.0)

xair is at v0.0.1. Before v1.0.0 there is NO backwards-compatibility commitment. Bump the version when API shape changes; no shims, no deprecation cycles.

## The Hard Rule

Before v1.0.0:

- ✅ Breaking changes to public API (CommandContext fields, dispatch() signature, infra/Provider Protocols) are FINE.
- ✅ Removing classes, functions, kwargs that exist in the published surface is FINE.
- ❌ Do NOT keep dead code "for backwards compat" or "in case someone is using it".
- ❌ Do NOT add `# DEPRECATED` markers as a substitute for actual deletion.

If you're adding a feature and the natural shape conflicts with existing code, delete the existing code and bump the patch/minor version. Cite the breakage in the commit body so anyone tracking xair (currently only bair) knows what to update.

## Why It Matters

The free-intelligence philosophy in `~/Documents/free-intelligence/.claude/CLAUDE.md` is explicit:

> "THIS APP HAS NOT BEEN LAUNCHED. NO USERS. NO DEADLINE.
> Therefore:
> - NO DEPRECATED CODE - Delete it, don't mark it deprecated
> - NO LEGACY CODE - Refactor it now, not 'later'
> - NO BACKWARD COMPATIBILITY HACKS - There's nothing to be compatible with"

xair inherits the same posture. The first refactor (commit `6ff5a9c`) deleted 1024 lines NET by ripping out `command_registry.py`'s `DeepMode` coupling and `dispatch.py`'s 400 lines of consumer-specific routing. Half of that was old "keep for compat" — without that anchor, the framework actually generalizes.

## How to Phrase a Breaking Commit

```
refactor(dispatch): generic registry-driven dispatcher, zero command knowledge

Previous: _COMMANDS = {"review","retro","remedy","revert"} hardcoded in dispatch.py.
New: dispatch() uses command_registry.get_handler(name); no specific names known.

BREAKING:
  - CommandContext.deep_mode field removed; use ctx.options.get("deep") instead.
  - _COMMAND_META dict removed; consumers call register_ack_meta("name", icon=…, label=…).
  - 398-line typer CLI in __main__.py removed; xair.__main__ is now a 28-line stub.
    Consumers re-implement their own Typer CLI if they want one (see bair).

Consumer migration: bair must update CommandContext field access + drop the
old typer subcommands. No deprecation shim was added.
```

Include `BREAKING:` keyword + concrete migration steps. The consumer maintains pace with xair, not the other way around.

## When This Rule Changes

The day xair hits v1.0.0 (or is published to PyPI under a stable promise), this rule becomes obsolete and the standard semver+deprecation cycle applies.

Until then: delete.

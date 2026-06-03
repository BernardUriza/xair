"""Generic dispatcher — pure registry-driven, knows ZERO specific commands.

The dispatcher's job is small and singular: parse the trigger comment
(or the explicit ``argv``), look up the matching handler in the registry,
build the I/O container, ack the comment, and run the handler.

NO hardcoded command names. NO imports of concrete pipelines. The
early dispatcher carried 400+ lines of consumer-specific routing
(claude-vs-gpt branching, deep-mode tier selection, review-via-executor
fallbacks) — every line of that has been pushed back into the consumer's
own pipeline handlers, where it belongs.

Usage::

    python -m xair <command>            # ad-hoc, reads PR_BODY env
    from xair.dispatch import dispatch  # programmatic; pass argv
"""

from __future__ import annotations

import os
import sys

from .command_registry import (
    CommandContext,
    DispatchResult,
    ack_command,
    get_handler,
    parse_comment,
    registered_commands,
)
from .infra.container import Container
from .log import logger


def _set_output(key: str, value: str) -> None:
    """Append a key=value pair to $GITHUB_OUTPUT, if the file is set."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def _resolve_context(argv: list[str]) -> CommandContext | None:
    """Build a CommandContext from explicit argv OR fall back to env-based
    GitHub-comment parsing.

    Priority:
      1. ``argv[0]`` non-empty → treat it as the command name; remaining
         ``argv`` tail becomes the comment body (so existing parser logic
         for key:value options still applies).
      2. ``COMMENT_BODY`` env var (set by the GitHub Actions trigger) →
         parse it via :func:`parse_comment`.
    """
    if argv:
        synthetic = f"/ai-{argv[0]} " + " ".join(argv[1:])
        return parse_comment(synthetic)
    body = os.environ.get("COMMENT_BODY", "")
    if not body:
        return None
    return parse_comment(body)


def dispatch(argv: list[str] | None = None) -> int:
    """Generic registry-driven dispatcher. Returns process exit code.

    Steps:
      1. Resolve a CommandContext (from argv or env).
      2. Look up the handler in the registry.
      3. Build a Container (override via subclass for consumer-specific
         providers).
      4. Ack the trigger comment (best-effort, silenced on error).
      5. Run the handler.
    """
    argv = argv if argv is not None else sys.argv[1:]
    ctx = _resolve_context(argv)
    if ctx is None:
        known = sorted(registered_commands())
        print("usage: python -m xair <command> [key:value ...]", file=sys.stderr)
        print(f"  registered commands: {known}", file=sys.stderr)
        return 2

    handler = get_handler(ctx.command)
    if handler is None:
        known = sorted(registered_commands())
        logger.error(f"unknown command {ctx.command!r}; registered: {known}")
        _set_output("command", ctx.command)
        _set_output("executed", "false")
        return 2

    container = Container.production()
    try:
        ack_command(container, ctx)
    except Exception as exc:  # noqa: BLE001 — ack is best-effort
        logger.warning(f"ack failed for /ai-{ctx.command}: {exc}")

    try:
        handler(ctx, container)
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        logger.exception(f"handler for /ai-{ctx.command} raised: {exc}")
        _set_output("command", ctx.command)
        _set_output("executed", "false")
        return 1

    result = DispatchResult(
        command=ctx.command,
        engine=ctx.engine,
        options=ctx.options,
        executed=True,
    )
    _set_output("command", result.command)
    _set_output("engine", result.engine)
    _set_output("executed", "true")
    return 0

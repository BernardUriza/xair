"""Command registry -- decorator-based dispatch for /ai-* commands.

Adding a new command = writing a handler function and decorating it.
The framework does NOT know any specific command; it only routes the
parsed comment to the registered handler. Consumers self-register at
import time::

    @command("score")
    def handle_score(ctx: CommandContext, container: Container) -> None:
        ...

This module is FRAMEWORK-GENERIC: it never imports a pipeline, a config
dataclass, or a consumer-specific enum. CLI-flag-like ``key:value``
tokens parsed out of a comment body land in ``CommandContext.options``;
consumers interpret them however they want (case-insensitive comparison
against their own enums, free-form string, etc.).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from .infra.constants import TEMPLATES_DIR
from .infra.container import Container
from .log import logger


# -- Command context (parsed input) ------------------------------------

@dataclass(frozen=True, slots=True)
class CommandContext:
    """Parsed, validated input for a command handler.

    ``options`` carries free-form ``key:value`` tokens parsed out of the
    trigger comment (e.g. ``engine:claude``, ``deep``, ``nodeep``,
    ``tier:tight``). Bare flags appear as ``{"flag_name": "true"}``.
    Consumers map options into their own typed config dataclasses.
    """

    command: str
    engine: str
    pr_num: str
    run_id: str
    repo: str
    options: dict[str, str] = field(default_factory=dict)
    guidance: str = ""
    raw_tail: str = ""


# -- Parsed event result -----------------------------------------------

@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What the dispatcher decided. Written to $GITHUB_OUTPUT for YAML routing."""

    command: str
    engine: str
    options: dict[str, str] = field(default_factory=dict)
    executed: bool = False


# -- Registry ----------------------------------------------------------

HandlerFn = Callable[[CommandContext, Container], None]

_REGISTRY: dict[str, HandlerFn] = {}
_ACK_META: dict[str, dict[str, str]] = {}


def command(name: str) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator to register a command handler."""
    def decorator(fn: HandlerFn) -> HandlerFn:
        _REGISTRY[name] = fn
        return fn
    return decorator


def register_ack_meta(name: str, *, icon: str, label: str) -> None:
    """Register an icon + label used when ack-ing the trigger comment.

    Optional — without registration, :func:`ack_command` falls back to a
    generic gear icon and the command name title-cased."""
    _ACK_META[name] = {"icon": icon, "label": label}


def get_handler(name: str) -> HandlerFn | None:
    """Look up a registered handler by command name."""
    return _REGISTRY.get(name)


def registered_commands() -> frozenset[str]:
    """Return all registered command names."""
    return frozenset(_REGISTRY)


# -- Parsing -----------------------------------------------------------

_COMMAND_RE = re.compile(r"^/ai-(\S+)(.*)", re.MULTILINE)
_KV_RE = re.compile(r"\b([a-zA-Z_][\w-]*):(\S+)")
_BARE_FLAG_RE = re.compile(r"(?<!\w):([a-zA-Z_][\w-]*)\b")  # `:flag` standalone

_DEFAULT_ENGINE = "gpt"
_KNOWN_ENGINES = frozenset({"gpt", "claude", "none"})


def _parse_options(text: str) -> tuple[str, dict[str, str]]:
    """Extract ``key:value`` tokens AND standalone ``\\bdeep\\b``-style
    bare flags from ``text``. Returns ``(engine, options_dict)``.

    ``engine`` is pulled out because it's the one option every consumer
    cares about (gpt|claude|none). All other key:value pairs land in
    ``options`` unchanged. Bare alphanumeric words like ``deep``,
    ``nodeep``, ``dryrun`` are also captured into options (value=``true``)
    so consumers don't have to re-parse the same string.
    """
    options: dict[str, str] = {}
    engine = _DEFAULT_ENGINE
    for k, v in _KV_RE.findall(text):
        key = k.lower()
        if key == "engine":
            val = v.lower()
            if val in _KNOWN_ENGINES:
                engine = val
            else:
                logger.warning(f"Unknown engine '{val}' -- falling back to {_DEFAULT_ENGINE}")
        else:
            options[key] = v
    # Bare flags — anything looking like a single lowercase word in the tail
    for word in re.findall(r"\b([a-z]{2,})\b", text):
        if word in {"engine", "the", "and", "for", "with"}:
            continue
        # Don't overwrite an explicit key:value
        options.setdefault(word, "true")
    return engine, options


def _parse_guidance(tail: str, after_match: str) -> str:
    """Extract freeform guidance text, stripping ``key:value`` tokens."""
    continuation = []
    for line in after_match.splitlines():
        if line.strip().startswith("/ai-"):
            break
        if line.strip():
            continuation.append(line.strip())

    raw = (tail + " " + " ".join(continuation)).strip() if (tail or continuation) else ""
    return _KV_RE.sub("", raw).strip()


def parse_comment(comment_body: str) -> CommandContext | None:
    """Parse a GitHub comment into a CommandContext. Returns None if no command found."""
    match = _COMMAND_RE.search(comment_body)
    if not match:
        return None

    cmd = match.group(1).lower()
    tail = (match.group(2) or "").strip()
    after = comment_body[match.end():]
    full_text = tail + " " + after

    engine, options = _parse_options(full_text)
    return CommandContext(
        command=cmd,
        engine=engine,
        pr_num=os.environ.get("PR_NUM", ""),
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        repo=os.environ.get("REPO", ""),
        options=options,
        guidance=_parse_guidance(tail, after),
        raw_tail=tail,
    )


# -- Ack helper --------------------------------------------------------

@lru_cache(maxsize=8)
def _load_template(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def ack_command(container: Container, ctx: CommandContext) -> None:
    """Post an acknowledgment reply to the trigger comment.

    Looks up the command's icon + label in the meta registry populated by
    :func:`register_ack_meta`. Unknown commands fall back to a generic
    gear icon and a title-cased name."""
    run_url = f"https://github.com/{ctx.repo}/actions/runs/{ctx.run_id}"
    meta = _ACK_META.get(ctx.command, {"icon": "⚙️", "label": ctx.command.title()})
    engine_tag = f" ({ctx.engine})" if ctx.engine != _DEFAULT_ENGINE else ""
    body = _load_template("ack").format(
        icon=meta["icon"],
        label=meta["label"] + engine_tag,
        run_id=ctx.run_id,
        run_url=run_url,
    )
    try:
        container.github.run_gh(
            "api", f"repos/{ctx.repo}/issues/{ctx.pr_num}/comments",
            "--method", "POST", "-f", f"body={body}",
            check=False,
        )
    except Exception:
        pass

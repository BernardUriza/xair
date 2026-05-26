"""Command registry -- decorator-based dispatch for /ai-* commands.

Adding a new command = writing a handler function and decorating it.
No modification to the router needed. Commands self-register.

    @command("score")
    def handle_score(ctx: CommandContext, container: Container) -> None:
        ...
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from ..config.review import DeepMode
from ..infra.constants import TEMPLATES_DIR
from ..infra.container import Container
from ..log import logger


# -- Command context (parsed input) ------------------------------------

@dataclass(frozen=True, slots=True)
class CommandContext:
    """Parsed, validated input for a command handler."""

    command: str
    engine: str
    pr_num: str
    run_id: str
    repo: str
    deep_mode: DeepMode = DeepMode.AUTO
    guidance: str = ""
    raw_tail: str = ""


# -- Parsed event result -----------------------------------------------

@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What the dispatcher decided. Written to $GITHUB_OUTPUT for YAML routing."""

    command: str
    engine: str
    deep_mode: DeepMode = DeepMode.AUTO
    executed: bool = False


# -- Registry ----------------------------------------------------------

HandlerFn = Callable[[CommandContext, Container], None]

_REGISTRY: dict[str, HandlerFn] = {}


def command(name: str) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator to register a command handler."""
    def decorator(fn: HandlerFn) -> HandlerFn:
        _REGISTRY[name] = fn
        return fn
    return decorator


def get_handler(name: str) -> HandlerFn | None:
    """Look up a registered handler by command name."""
    return _REGISTRY.get(name)


def registered_commands() -> frozenset[str]:
    """Return all registered command names."""
    return frozenset(_REGISTRY)


# -- Parsing -----------------------------------------------------------

_COMMAND_RE = re.compile(r"^/ai-(\S+)(.*)", re.MULTILINE)
_ENGINE_RE = re.compile(r"engine:\s*(\S+)", re.IGNORECASE)
_DEEP_RE = re.compile(r"\bdeep\b", re.IGNORECASE)
_NODEEP_RE = re.compile(r"\bnodeep\b", re.IGNORECASE)

_ENGINES = {"gpt", "claude"}
_DEFAULT_ENGINE = "gpt"


def _parse_engine(text: str) -> str:
    """Extract engine from text like 'engine:claude'."""
    match = _ENGINE_RE.search(text)
    if match:
        engine = match.group(1).lower()
        if engine in _ENGINES:
            return engine
        logger.warning(f"Unknown engine '{engine}' -- falling back to {_DEFAULT_ENGINE}")
    return _DEFAULT_ENGINE


def _parse_deep_mode(text: str) -> DeepMode:
    """Tri-state: nodeep > deep > auto."""
    if _NODEEP_RE.search(text):
        return DeepMode.DISABLED
    if _DEEP_RE.search(text):
        return DeepMode.FORCED
    return DeepMode.AUTO


def _parse_guidance(tail: str, after_match: str) -> str:
    """Extract freeform guidance text, stripping parameters."""
    continuation = []
    for line in after_match.splitlines():
        if line.strip().startswith("/ai-"):
            break
        if line.strip():
            continuation.append(line.strip())

    raw = (tail + " " + " ".join(continuation)).strip() if (tail or continuation) else ""
    cleaned = _ENGINE_RE.sub("", raw)
    cleaned = _NODEEP_RE.sub("", cleaned)
    cleaned = _DEEP_RE.sub("", cleaned)
    return cleaned.strip()


def parse_comment(comment_body: str) -> CommandContext | None:
    """Parse a GitHub comment into a CommandContext. Returns None if no command found."""
    match = _COMMAND_RE.search(comment_body)
    if not match:
        return None

    cmd = match.group(1).lower()
    tail = (match.group(2) or "").strip()
    after = comment_body[match.end():]
    full_text = tail + " " + after

    return CommandContext(
        command=cmd,
        engine=_parse_engine(full_text),
        pr_num=os.environ.get("PR_NUM", ""),
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        repo=os.environ.get("REPO", ""),
        deep_mode=_parse_deep_mode(full_text),
        guidance=_parse_guidance(tail, after),
        raw_tail=tail,
    )


# -- Ack helper --------------------------------------------------------

_COMMAND_META: dict[str, dict[str, str]] = {
    "review":        {"icon": "\U0001f916", "label": "Review"},
    "retro":         {"icon": "\U0001f50d", "label": "Retro"},
    "prompt-deploy": {"icon": "\U0001f4e6", "label": "Prompt Deploy"},
}


@lru_cache(maxsize=8)
def _load_template(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def ack_command(container: Container, ctx: CommandContext) -> None:
    """Post an acknowledgment reply to the trigger comment."""
    run_url = f"https://github.com/{ctx.repo}/actions/runs/{ctx.run_id}"
    meta = _COMMAND_META.get(ctx.command, {"icon": "\u2699\ufe0f", "label": ctx.command.title()})
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

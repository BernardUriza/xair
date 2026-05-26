"""Build the rendered prompt for Claude Code Action.

Reads the claude-review.md template, injects learnings and rules,
writes the final prompt to a file that the YAML workflow passes
to anthropics/claude-code-action@v1.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from ..infra.constants import TEMPLATES_DIR
from ..log import logger

# claude-review.md lives in prompts/ (sibling to base.md, frontend.md, etc.)
_PROMPT_DIR = TEMPLATES_DIR.parent
_RULES_DIR = Path("ai-rules/rules/shared")  # relative to repo root at CI time
_LEARNINGS_DIR = Path("learnings")
_OUTPUT_FILE = Path("/tmp/claude-prompt.md")


@lru_cache(maxsize=1)
def _load_template() -> str:
    path = _PROMPT_DIR / "claude-review.md"
    if not path.exists():
        raise FileNotFoundError(f"Claude review template not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_rules() -> str:
    """Read all shared rule files into a single block."""
    if not _RULES_DIR.exists():
        return ""

    parts = ["## Engineering Rules (from team standards)\n"]
    for rule_file in sorted(_RULES_DIR.glob("*.md")):
        content = rule_file.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
            parts.append("")
    return "\n".join(parts) if len(parts) > 1 else ""


def _load_learnings(variant: str) -> str:
    """Read the learnings YAML file as raw text for Claude to parse."""
    learnings_file = _LEARNINGS_DIR / f"{variant}.yml"
    if not learnings_file.exists():
        return ""

    content = learnings_file.read_text(encoding="utf-8").strip()
    if not content:
        return ""

    return f"## Validated Policies (from past reviews)\n\n```yaml\n{content}\n```"


def build_claude_prompt(
    repo: str,
    pr_number: str,
    variant: str,
) -> str:
    """Render the full Claude Code review prompt with learnings + rules."""
    template = _load_template()

    learnings = _load_learnings(variant)
    rules = _load_rules()

    return template.format(
        repo=repo,
        pr_number=pr_number,
        variant=variant,
        learnings_section=learnings,
        rules_section=rules,
    )


def write_claude_prompt_file(
    repo: str,
    pr_number: str,
    variant: str,
) -> Path:
    """Build the prompt and write to /tmp/claude-prompt.md for YAML to consume."""
    prompt = build_claude_prompt(repo, pr_number, variant)
    _OUTPUT_FILE.write_text(prompt, encoding="utf-8")
    logger.info(f"Claude prompt written to {_OUTPUT_FILE} ({len(prompt)} chars)")
    return _OUTPUT_FILE

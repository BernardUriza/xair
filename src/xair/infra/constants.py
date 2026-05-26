"""Infrastructure constants — paths, artifact keys, env var names, defaults.

No domain logic. No I/O. Just configuration values.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..domain.exceptions import ConfigError

# ── Paths ────────────────────────────────────────────────────────────

# infra/ → xair/ → scripts/ → .github/ → repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
TEMPLATES_DIR = _REPO_ROOT / "prompts" / "templates"

# ── Git bot identity (GitHub Actions bot) ────────────────────────────

GIT_BOT_NAME = "github-actions[bot]"
GIT_BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
LEARNINGS_CLONE_DIR = "/tmp/dotgithub"
LEARNINGS_REPO = "xair-org/.github"


def require_env(name: str) -> str:
    """Read a required environment variable or raise ConfigError."""
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set")
    return value


# ── Artifact keys (tmp filenames) ────────────────────────────────────

DIFF_INPUT = "diff-input.txt"
REVIEW_JSON = "review.json"
LEARNINGS_CONTEXT = "learnings-context.txt"
RULES_CONTEXT = "rules-context.txt"
RESOLVED_CONTEXT = "resolved-context.txt"
PRIOR_ART_CONTEXT = "prior-art-context.txt"
CSS_HEALTH = "css-health.txt"
CI_STATUS = "ci-status.txt"
FULL_FILES_CONTEXT = "full-files-context.txt"
SELECTED_RULES = "selected-rules.txt"
CHANGELOG_OUTPUT = "changelog-output.json"

# ── Defaults ─────────────────────────────────────────────────────────

# Single Source of Truth for the OpenAI model used across every stage
# (classifier, gatherers, narrator, review, changelog, retro). Hardcoded
# invariant — no env override, no per-pipeline override. If this needs to
# change, change it here and nowhere else.
#
# Brython mirror at xair/frontend/py/chat.py must be updated by hand
# (cross-runtime, can't import).
OPENAI_MODEL = "gpt-5.5"

# Backward-compat alias. New code MUST import OPENAI_MODEL.
DEFAULT_MODEL = OPENAI_MODEL
DEFAULT_MAX_DIFF_BYTES = 120_000
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_LINES_PER_FILE = 500
DEFAULT_MAX_TOTAL_FILE_BYTES = 80_000
DEFAULT_MAX_RULES_BYTES = 25_000
DEFAULT_REPLY_MAX_CHARS = 2000

# ── Env var names (used by gatherers for GITHUB_ENV writes) ──────────

ENV_TRUNCATED = "truncated"

# ── Rules triage ─────────────────────────────────────────────────────

# Resolve rules dir from repo root (works in CI and locally).
# CI: checkout lands at GITHUB_WORKSPACE, _REPO_ROOT points there.
# Local: _REPO_ROOT resolves via __file__ to the github-org clone.
RULES_DIR = _REPO_ROOT / "ai-rules" / "rules" / "shared"

# ── OpenAI API ───────────────────────────────────────────────────────

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

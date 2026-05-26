"""Centralized logging — loguru configured for CI + local use.

Usage in any module:
    from xair.log import logger

Levels:
    logger.info()    — pipeline progress (user-visible: "[1/7] DIFF")
    logger.debug()   — diagnostics (token counts, tier sizes, timing)
    logger.warning() — recoverable issues
    logger.error()   — failures

The default level is DEBUG (show everything). Pass --quiet to the CLI
to raise it to INFO (hide diagnostics).

GitHub Actions annotations (::notice::, ::warning::, ::error::) are
handled separately by ActionsIO — loguru does NOT emit those.
"""

from __future__ import annotations

import os
import sys

from loguru import logger

# Remove loguru's default handler (stderr with colors)
logger.remove()

# Single handler: stdout, no colors in CI (GitHub Actions sets CI=true),
# colors locally for readability. Format matches our existing print style.
_CI = os.environ.get("CI", "").lower() == "true"

logger.add(
    sys.stdout,
    format="{message}" if _CI else "<level>{level.name:8s}</level> | {message}",
    level=os.environ.get("LOG_LEVEL", "DEBUG").upper(),
    colorize=not _CI,
)

__all__ = ["logger"]

"""Structured exceptions — replace sys.exit() throughout the codebase."""

from __future__ import annotations


class AIReviewerError(Exception):
    """Base exception for all xair errors."""


class ConfigError(AIReviewerError):
    """Missing or invalid configuration (env vars, files, args)."""


class LlmError(AIReviewerError):
    """LLM provider failure (API error, timeout, rate limit, invalid response)."""

    def __init__(self, message: str, status_code: int = 0, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(message)


class ProviderError(AIReviewerError):
    """Infrastructure provider failure (GitHub CLI, file store, Slack)."""


class ValidationError(AIReviewerError):
    """LLM output failed schema validation or guardrail check."""

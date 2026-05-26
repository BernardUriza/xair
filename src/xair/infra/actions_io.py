"""ActionsIO implementations — GitHub Actions output + null for tests."""

from __future__ import annotations

import os

from ..log import logger


class GitHubActionsIO:
    """Writes to $GITHUB_OUTPUT, $GITHUB_ENV, $GITHUB_STEP_SUMMARY."""

    def set_output(self, key: str, value: str) -> None:
        output_file = os.environ.get("GITHUB_OUTPUT", "")
        if not output_file:
            logger.debug(f"[output] {key}={value[:120]}")
            return
        with open(output_file, "a", encoding="utf-8") as f:
            if "\n" in value:
                delimiter = f"{key.upper()}_EOF"
                f.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{key}={value}\n")

    def set_env(self, key: str, value: str) -> None:
        env_file = os.environ.get("GITHUB_ENV", "")
        if not env_file:
            logger.debug(f"[env] {key}={value}")
            return
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")

    def write_summary(self, content: str) -> None:
        summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if not summary_file:
            logger.debug(f"[summary] {len(content)} bytes")
            return
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(content)

    def replace_summary(self, content: str) -> None:
        """Overwrite the step summary file. Used for streaming partial-state
        renders where each call should replace the previous content rather
        than append. GitHub Actions reads $GITHUB_STEP_SUMMARY on every UI
        refresh — the latest write wins."""
        summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if not summary_file:
            logger.debug(f"[summary:replace] {len(content)} bytes")
            return
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(content)

    def notice(self, msg: str) -> None:
        logger.info(f"::notice::{msg}")

    def warning(self, msg: str) -> None:
        logger.warning(f"::warning::{msg}")

    def error(self, msg: str) -> None:
        logger.error(f"::error::{msg}")

    def emit(self, outputs: dict[str, str]) -> None:
        for key, value in outputs.items():
            self.set_output(key, value)


class NullActionsIO:
    """Swallows all output — for tests."""

    def __init__(self) -> None:
        self.outputs: dict[str, str] = {}
        self.env_vars: dict[str, str] = {}
        self.summaries: list[str] = []
        self.notices: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def set_output(self, key: str, value: str) -> None:
        self.outputs[key] = value

    def set_env(self, key: str, value: str) -> None:
        self.env_vars[key] = value

    def write_summary(self, content: str) -> None:
        self.summaries.append(content)

    def replace_summary(self, content: str) -> None:
        """Mirror overwrite semantics: keep only the latest content. Tests that
        want to inspect every intermediate render should read ``summaries[-1]``
        after each call, or use ``write_summary`` for append semantics."""
        if self.summaries:
            self.summaries[-1] = content
        else:
            self.summaries.append(content)

    def notice(self, msg: str) -> None:
        self.notices.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def emit(self, outputs: dict[str, str]) -> None:
        for key, value in outputs.items():
            self.set_output(key, value)

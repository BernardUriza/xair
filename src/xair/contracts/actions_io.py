"""ActionsIO — primitivas de GitHub Actions output.

Inyectado en stages que necesitan emitir outputs, summary, notices, warnings.
"""

from __future__ import annotations

from typing import Protocol


class ActionsIO(Protocol):
    """GitHub Actions output primitives."""

    def set_output(self, key: str, value: str) -> None: ...

    def set_env(self, key: str, value: str) -> None: ...

    def write_summary(self, content: str) -> None: ...

    def replace_summary(self, content: str) -> None:
        """Overwrite $GITHUB_STEP_SUMMARY with ``content``.

        Distinct from ``write_summary`` (which appends): used for streaming
        progress updates where every call should replace the file contents.
        GitHub Actions reads ``$GITHUB_STEP_SUMMARY`` on each UI refresh and
        renders the latest content, so overwrite is the right semantics for
        progressive rendering.
        """
        ...

    def notice(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...

    def emit(self, outputs: dict[str, str]) -> None:
        """Write multiple outputs at once."""
        ...

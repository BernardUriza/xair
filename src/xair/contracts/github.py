"""GitHubClient — wrapper sobre los CLIs `gh` y `git`."""

from __future__ import annotations

from typing import Protocol


class GitHubClient(Protocol):
    """Wraps `gh` y `git` CLIs para ejecutar comandos contra GitHub y el repo."""

    def run_gh(self, *args: str, check: bool = True, input_data: str | None = None) -> str: ...

    def run_git(self, *args: str, check: bool = True, cwd: str | None = None) -> str: ...

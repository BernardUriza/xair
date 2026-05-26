"""GitHubClient implementation — wraps gh and git CLIs via subprocess."""

from __future__ import annotations

import subprocess
import sys


class SubprocessGitHubClient:
    """Runs ``gh`` and ``git`` as subprocesses."""

    def run_gh(self, *args: str, check: bool = True, input_data: str | None = None) -> str:
        result = subprocess.run(
            ["gh", *args],
            input=input_data,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=check,
        )
        # When check=False and the command failed with empty stdout, surface
        # stderr so callers don't get a silent "API returned empty response".
        # The publisher (publishing.py:_post_api) swallows the result when
        # stdout is empty; without this, GitHub's actual rejection message is
        # invisible.
        if not check and result.returncode != 0 and not result.stdout and result.stderr:
            print(
                f"  [run_gh] gh exited {result.returncode}: {result.stderr.strip()[:500]}",
                file=sys.stderr,
                flush=True,
            )
        return result.stdout

    def run_git(self, *args: str, check: bool = True, cwd: str | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=check,
            cwd=cwd,
        )
        return result.stdout

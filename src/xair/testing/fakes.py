"""Test doubles — fakes for all four protocols."""

from __future__ import annotations

from ..infra.actions_io import NullActionsIO
from ..infra.file_store import InMemoryFileStore


class FakeLlm:
    """Returns canned responses keyed by model name or call order."""

    def __init__(self, responses: list[dict] | None = None) -> None:
        self._responses = list(responses) if responses else []
        self._call_index = 0
        self.calls: list[dict] = []

    def call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool = True,
    ) -> dict:
        self.calls.append({
            "system": system,
            "user": user,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "json_mode": json_mode,
        })
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        return {"summary": "fake review", "findings": []}


class FakeGitHub:
    """Records calls and returns canned stdout strings."""

    def __init__(self, gh_responses: dict[str, str] | None = None) -> None:
        self._gh_responses = gh_responses or {}
        self.gh_calls: list[tuple[str, ...]] = []
        self.git_calls: list[tuple[str, ...]] = []

    def run_gh(self, *args: str, check: bool = True, input_data: str | None = None) -> str:
        self.gh_calls.append(args)
        # Match on first arg that looks like an API path
        for key, value in self._gh_responses.items():
            if key in args:
                return value
        return ""

    def run_git(self, *args: str, check: bool = True, cwd: str | None = None) -> str:
        self.git_calls.append(args)
        return ""


def make_test_container(
    *,
    llm_responses: list[dict] | None = None,
    gh_responses: dict[str, str] | None = None,
    store_data: dict[str, str] | None = None,
) -> tuple:
    """Build (Container-like namespace, individual fakes) for tests.

    Returns a tuple of (container, llm, github, store, actions) so tests
    can inspect individual fakes.
    """
    from ..infra.container import Container

    llm = FakeLlm(llm_responses)
    github = FakeGitHub(gh_responses)
    store = InMemoryFileStore(store_data)
    actions = NullActionsIO()
    container = Container(llm=llm, github=github, store=store, actions=actions)
    return container, llm, github, store, actions

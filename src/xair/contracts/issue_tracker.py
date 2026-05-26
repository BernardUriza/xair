"""IssueTrackerClient — tracker-agnostic Protocol.

Histórico: este Protocol se llamó `LinearClient` durante la migración de
Linear a Plane (April 2026). Ahora todos los call sites importan
`IssueTrackerClient`; el alias `LinearClient` fue removido en el rename
tracker-agnostic (2026-05).
"""

from __future__ import annotations

from typing import Any, Protocol


class IssueTrackerClient(Protocol):
    """Lee y actualiza issues — implementado por PlaneClient.

    Los retornos son `list[Any]` (no `list[object]`) para mantener covarianza
    con retornos concretos `list[TrackerIssue]` / `list[PlaneIssue]`.
    """

    def list_issues(self, team_key: str = "VIS") -> list[Any]: ...

    def get_issue(self, identifier: str) -> Any: ...

    def get_workflow_states(self, team_id: str) -> list[Any]: ...

    def update_state(self, issue_id: str, state_id: str) -> None: ...

    def add_comment(self, issue_id: str, body: str) -> None: ...

    def add_attachment(self, issue_id: str, url: str, title: str) -> None: ...

    def transition_to(self, issue: Any, target_type: str) -> None: ...

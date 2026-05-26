"""Publisher — cómo se materializa una decisión final en el mundo.

Después del verdict de Claude, el Publisher decide cómo el resultado llega
al destinatario externo: push a un branch, comment en un PR, mensaje a Slack,
update a Plane.

Tres implementaciones canónicas (ver sección 11 del plan):

- `PushBranchPublisher` — push de los 2 commits (xair-codex + xair-claude)
  al PR branch. Usado por work, remedy, prompt-deploy.
- `PostReviewPublisher` — review consolidada al PR. Usado por review.
- `PostSlackPublisher` — mensaje a Slack o Plane. Usado por changelog, retro.

El Publisher es la última capa antes de side-effects externos. Por eso vive
detrás de policies/gating — no puede correr sin pasar `should_publish()`.
"""

from __future__ import annotations

from typing import Any, Protocol


class Publisher(Protocol):
    """Materializa una decisión final en un destino externo (PR, Slack, etc.)."""

    def publish(self, verdict: Any, payload: Any) -> str:
        """Publica el resultado y devuelve un identificador externo
        (URL del comment, message ts, push SHA) para auditoría.
        """
        ...

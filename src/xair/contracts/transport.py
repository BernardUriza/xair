"""Transport — cómo viaja una propuesta entre stages adversariales.

Patrón clave del diseño multi-perspective: Codex genera, Claude valida.
El Transport define cómo Codex le pasa el trabajo a Claude.

Dos implementaciones canónicas (ver sección 11 del plan):

- `GitCommitTransport` — Codex commitea al worktree, Claude lee con
  `git show`. Usado por work, remedy, prompt-deploy.
- `TextProposalTransport` — Codex devuelve string estructurado, Claude lo
  recibe en su prompt. Usado por review, changelog, retro.

El Protocol es genérico: cualquier mecanismo que materialice una propuesta
de un stage al siguiente cumple este contrato.
"""

from __future__ import annotations

from typing import Any, Protocol


class Transport(Protocol):
    """Materializa una propuesta entre stages adversariales.

    Implementaciones determinan dónde vive la propuesta entre el momento en
    que el stage productor termina y el stage consumidor empieza:
    git history, string en prompt, fichero compartido, message bus.
    """

    def deliver(self, proposal: Any) -> str:
        """Materializa la propuesta y devuelve un identificador trazable
        (commit SHA, message id, file path) que el consumidor puede leer.
        """
        ...

    def receive(self, identifier: str) -> Any:
        """Lee la propuesta materializada. Llamado por el stage consumidor.

        Garantiza que la propuesta es idéntica a lo que el productor entregó —
        sin mutación silenciosa, sin re-serialización lossy.
        """
        ...

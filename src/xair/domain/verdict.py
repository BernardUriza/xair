"""Verdict — decisión tipada del claude_stage en pipelines multi-perspective.

Cuatro outcomes posibles después de que Claude revisa lo que Codex produjo
(ver `frontend/diagrams/xair-multi-perspective.html` sección 9):

- ACCEPT     — Codex tenía razón; commitear sin cambios
- REFINE     — Codex está cerca pero hay que ajustar; Claude edita y commitea
- REVERT     — Codex está mal; revertir su commit
- REGENERATE — Codex está mal; revertir y rehacer desde cero

El Verdict NO es un string ("accept", "refine") porque eso permitiría
typos silenciosos. Es un Enum con tres campos adicionales:

- decision    (Verdict.Decision)        — qué hacer
- confidence  (Verdict.Confidence)      — qué tan seguro está Claude
- reason      (str)                     — explicación corta para el log

Confidence < HIGH activa policies de escalación a human review (F4 escalation.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    """Las cuatro transiciones legales del Verdict FSM."""

    ACCEPT = "accept"
    REFINE = "refine"
    REVERT = "revert"
    REGENERATE = "regenerate"


class Confidence(str, Enum):
    """Nivel de certeza con que Claude llegó al Verdict.

    HIGH:   evidencia clara y suficiente, decisión sin ambigüedad
    MEDIUM: hay buena base pero un humano podría disentir razonablemente
    LOW:    decisión basada en heurística débil; HITL recomendado
    ABSTAIN:no hay evidencia suficiente para decidir; HITL mandatorio
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class Verdict:
    """Decisión final del claude_stage en una pipeline multi-perspective.

    Inmutable por construcción — un Verdict se emite una vez y queda en el
    audit trail. Modificar la decisión requiere emitir un Verdict nuevo, no
    mutar el existente.
    """

    decision: Decision
    confidence: Confidence
    reason: str = ""

    @property
    def is_terminal(self) -> bool:
        """True si el verdict no requiere más procesamiento (ACCEPT, REVERT)."""
        return self.decision in (Decision.ACCEPT, Decision.REVERT)

    @property
    def requires_edit(self) -> bool:
        """True si Claude necesita modificar el worktree (REFINE, REGENERATE)."""
        return self.decision in (Decision.REFINE, Decision.REGENERATE)

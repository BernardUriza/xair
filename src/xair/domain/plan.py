"""ExecutionPlan — clasificación del classifier que enruta el resto del pipeline.

El planner (gpt-5.5 hardcoded — ver `infra/constants.py`) clasifica el input
en una de tres complejidades. La clasificación determina cuál Claude usa el
synthesis stage (sonnet vs opus, deep_analysis on/off):

- SIMPLE  — typo, rename, fix de una sola línea       → sonnet, deep=false
- MEDIUM  — multi-file, sin auth/security             → sonnet, deep=true
- COMPLEX — arch change, security, >500 LOC           → opus, deep=true

Codex SIEMPRE corre con gpt-5.5, no enrutado por el clasificador
(`frontend/diagrams/xair-multi-perspective.html` sección 7).

Flags adicionales del Plan permiten override manual:
- force_opus           : bypasea la tabla, fuerza opus
- disable_classifier   : default a MEDIUM sin invocar al planner
- requires_security    : flag explícito que activa security_pass aunque la
                          clasificación no lo hubiera detectado
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Complexity(str, Enum):
    """Las tres clases del classifier."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Plan de ejecución producido por el classifier — input a las policies.

    El Plan es DATOS — viaja por el pipeline como entrada inmutable a cada
    Stage. Las policies (F4) lo consumen para decidir routing y gating.
    """

    complexity: Complexity
    reason: str = ""
    # Flags de override
    force_opus: bool = False
    disable_classifier: bool = False
    requires_security: bool = False
    # Hint del classifier sobre dominios sensibles
    legal_domain_touch: bool = False
    auth_or_security_touch: bool = False

    @property
    def effective_complexity(self) -> Complexity:
        """Complexity efectiva después de aplicar overrides."""
        if self.force_opus:
            return Complexity.COMPLEX
        return self.complexity

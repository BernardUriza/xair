"""Gating policies — debe el verdict materializarse en el mundo, requiere humano.

`should_publish` es la última gate antes de side-effects externos (push, PR
comment, Slack post). Si el verdict es REVERT con baja confianza, el pipeline
NO debería propagar al Publisher.

`requires_human` decide si el pipeline pausa para HumanReviewStage. La regla
clave del plan v7 sección 6 (lección agnihotry production):

> "el adversarial loop captura errores de implementación, no incomprensión
>  de feature."

Por eso `requires_human` NO se activa por desacuerdo entre Codex y Claude
(eso lo resuelve el Verdict FSM). Se activa por:

1. Confidence ABSTAIN o LOW
2. Plan flagea legal_domain_touch o auth_or_security_touch
3. Verdict REGENERATE con confidence < HIGH (señal de plan questionable)
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.plan import ExecutionPlan
from ..domain.verdict import Confidence, Decision, Verdict


@dataclass(frozen=True, slots=True)
class GateResult:
    """Resultado de una gate: pasa, no pasa, motivo.

    Decisión binaria (allow) más explicación. La explicación va al log y al
    audit trail — el future reviewer entiende POR QUÉ el pipeline se detuvo.
    """

    allow: bool
    reason: str


def should_publish(verdict: Verdict, plan: ExecutionPlan) -> GateResult:
    """Permite publicar el resultado al destino externo.

    Bloquea si:
    - Confidence es ABSTAIN (Claude no pudo decidir → no se publica nada)
    - REVERT con confidence < HIGH (revertir es destructivo; baja confianza
      sugiere que el revert mismo puede estar mal)
    """
    if verdict.confidence is Confidence.ABSTAIN:
        return GateResult(allow=False, reason="verdict abstained — no publish")
    if verdict.decision is Decision.REVERT and verdict.confidence is not Confidence.HIGH:
        return GateResult(
            allow=False,
            reason=f"REVERT with confidence={verdict.confidence.value} blocks publish",
        )
    return GateResult(allow=True, reason="gate passed")


def requires_human(verdict: Verdict, plan: ExecutionPlan) -> GateResult:
    """True si el pipeline debe pausar para HumanReviewStage.

    Ver plan v7 sección 6 — HITL es para WRONG REQUIREMENTS o legal/security
    semantics, NO para implementation errors (esos los maneja el Verdict FSM).
    """
    if verdict.confidence is Confidence.ABSTAIN:
        return GateResult(allow=True, reason="confidence ABSTAIN — HITL mandatory")
    if verdict.confidence is Confidence.LOW:
        return GateResult(allow=True, reason="confidence LOW — HITL recommended")
    if plan.legal_domain_touch:
        return GateResult(allow=True, reason="plan touches legal-domain semantics")
    if plan.auth_or_security_touch:
        return GateResult(allow=True, reason="plan touches auth/security")
    if verdict.decision is Decision.REGENERATE and verdict.confidence is not Confidence.HIGH:
        return GateResult(
            allow=True,
            reason="REGENERATE with non-HIGH confidence — plan likely questionable",
        )
    return GateResult(allow=False, reason="no HITL trigger conditions met")

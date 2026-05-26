"""Escalation policies — cuándo escalar a humano por baja confianza o
requirements ambiguos.

Plan v7 sección 6 (lección agnihotry production):

    "the adversarial loop catches implementation errors,
     not feature misunderstandings."

Por eso ``needs_escalation`` NO se activa por desacuerdo entre Codex y Claude
(eso lo resuelve el Verdict FSM). Se activa por:

1. Confidence ABSTAIN o LOW — Claude declaró que no pudo decidir bien
2. ``wrong_requirements_signal`` — el plan o el verdict tiene marcas de scope ambiguo
3. Legal-domain o auth/security touch en el plan + Verdict no-trivial

Estas funciones son las puertas que llevan al ``HumanReviewStage`` (F-future).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.plan import ExecutionPlan
from ..domain.verdict import Confidence, Decision, Verdict


@dataclass(frozen=True, slots=True)
class EscalationReason:
    """Por qué el pipeline pide intervención humana."""

    needed: bool
    code: str           # token corto para logs/dashboards
    explanation: str    # frase para el humano que recibe la escalación


def wrong_requirements_signal(plan: ExecutionPlan, verdict: Verdict) -> bool:
    """True si hay señales de que el problema es WRONG REQUIREMENTS, no impl.

    Heurística defendible:
    - Verdict REGENERATE con confidence != HIGH es señal de "el plan estaba mal,
      no la implementación de Codex"
    - REVERT con confidence != HIGH puede ser "Codex no entendió la tarea"
    - Plan con legal_domain_touch + verdict que no es ACCEPT → revisión humana
    """
    if verdict.decision is Decision.REGENERATE and verdict.confidence is not Confidence.HIGH:
        return True
    if verdict.decision is Decision.REVERT and verdict.confidence is not Confidence.HIGH:
        return True
    if plan.legal_domain_touch and verdict.decision is not Decision.ACCEPT:
        return True
    return False


def needs_escalation(plan: ExecutionPlan, verdict: Verdict) -> EscalationReason:
    """Decide si HumanReviewStage debe activarse para este (plan, verdict).

    Orden de checks importa: el primer match gana, así el ``code`` y la
    ``explanation`` reflejan la causa más fuerte. Logs/dashboards filtran por
    ``code`` para entender la mezcla de razones a través de muchos runs.
    """
    if verdict.confidence is Confidence.ABSTAIN:
        return EscalationReason(
            needed=True,
            code="abstain",
            explanation="Claude abstained — insufficient evidence to decide",
        )
    if verdict.confidence is Confidence.LOW:
        return EscalationReason(
            needed=True,
            code="low_confidence",
            explanation="Claude reached a verdict but with low confidence",
        )
    if plan.legal_domain_touch and verdict.decision is not Decision.ACCEPT:
        return EscalationReason(
            needed=True,
            code="legal_domain",
            explanation="plan touches legal-domain semantics and verdict is not ACCEPT",
        )
    if plan.auth_or_security_touch and verdict.decision is not Decision.ACCEPT:
        return EscalationReason(
            needed=True,
            code="auth_security",
            explanation="plan touches auth/security and verdict is not ACCEPT",
        )
    if wrong_requirements_signal(plan, verdict):
        return EscalationReason(
            needed=True,
            code="wrong_requirements",
            explanation="signal suggests requirements are wrong, not just implementation",
        )
    return EscalationReason(
        needed=False,
        code="none",
        explanation="no escalation triggers fired",
    )

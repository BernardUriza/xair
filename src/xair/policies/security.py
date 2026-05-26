"""Security policies — security_pass mandatory hook + scan gating.

Plan v7 sección 5 + Sherlock Forensics 2026: 31% de vulnerabilidades
AI-introduced vienen de helpers, utilities, y config tweaks. Por eso el
security_pass es **mandatory hook** en el executor, no un tool opcional
que el agente puede ignorar.

Estas funciones son puras — reciben metadata sobre el diff (cuántos archivos
helpers, si hay cambios CORS/IAM/env, si auth se tocó) y devuelven decisiones.
NO leen el diff ni invocan al scanner — eso lo hace el security_pass stage,
que llama a estas policies para decidir QUÉ escanear y SI el resultado pasa.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.plan import ExecutionPlan


@dataclass(frozen=True, slots=True)
class DiffSignals:
    """Metadata sobre el diff — alimentado por gather_stage.

    Los nombres de campos coinciden con los riesgos del Sherlock 2026 report.
    """

    helpers_touched: int = 0          # archivos en utils/, helpers/, lib/ con cambios
    config_touched: int = 0           # CORS, IAM, env vars, secrets references
    auth_files_touched: int = 0       # archivos en auth/, middleware/, guards/
    input_handling_touched: int = 0   # archivos que validan/parsean input externo


@dataclass(frozen=True, slots=True)
class SecurityGate:
    """Resultado de una decisión de seguridad."""

    pass_: bool
    severity: str  # "info" | "warn" | "block"
    reason: str


def needs_security_pass(diff: DiffSignals, plan: ExecutionPlan) -> bool:
    """True si el security_pass stage debe correr para este pipeline.

    Casi siempre devuelve True. Las excepciones (devuelve False) son:
    - El plan explícitamente disabilita security via flag (raro, casi nunca)
    - El diff está completamente vacío en categorías de riesgo

    El sesgo es FAIL-OPEN para seguridad: ante la duda, corre el pass.
    """
    # Override explícito del plan
    if plan.requires_security:
        return True

    # Si CUALQUIER categoría de riesgo está tocada, corre el pass
    if (
        diff.helpers_touched > 0
        or diff.config_touched > 0
        or diff.auth_files_touched > 0
        or diff.input_handling_touched > 0
    ):
        return True

    # Diff completamente fuera de categorías de riesgo: skip permitido
    return False


def gate_security(scan_findings: list[dict], plan: ExecutionPlan) -> SecurityGate:
    """Decide si el resultado del scanner deja pasar el pipeline o lo bloquea.

    `scan_findings` es la lista de hallazgos producida por el security_pass
    stage (no por esta función — esta es pura). Cada finding es un dict con
    al menos `severity`: 'critical' | 'high' | 'medium' | 'low' | 'info'.

    Reglas:
    - Cualquier `critical` o `high` → BLOCK (pipeline detenido, escala)
    - Solo `medium` con plan.auth_or_security_touch → BLOCK
    - Solo `medium` sin auth touch → WARN (continúa, registra)
    - Solo `low`/`info` → INFO (continúa silenciosamente)
    """
    severities = [f.get("severity", "info").lower() for f in scan_findings]

    if any(s in ("critical", "high") for s in severities):
        return SecurityGate(
            pass_=False,
            severity="block",
            reason=f"{sum(1 for s in severities if s in ('critical', 'high'))} high-severity findings",
        )

    medium_count = sum(1 for s in severities if s == "medium")
    if medium_count > 0 and plan.auth_or_security_touch:
        return SecurityGate(
            pass_=False,
            severity="block",
            reason=f"{medium_count} medium findings on auth/security-touched diff",
        )
    if medium_count > 0:
        return SecurityGate(
            pass_=True,
            severity="warn",
            reason=f"{medium_count} medium findings (non-auth diff) — continuing with warning",
        )

    return SecurityGate(pass_=True, severity="info", reason="no significant findings")

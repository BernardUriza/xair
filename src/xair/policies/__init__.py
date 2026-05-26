"""Policies — funciones puras de decisión, separadas de los pipelines.

Cada módulo expone una o más funciones puras (sin red, sin disco, sin LLM)
que reciben datos y devuelven decisiones. Son testables con pytest sin mocks
de proveedores externos.

Anti-pattern que esto reemplaza: ``if config.use_codex: ...`` embebido en
``pipelines/resolve.py``. Cuando la lógica de routing/gating vive en pipelines,
cambiar política implica tocar production code; cuando vive en
``policies/``, cambiar política es cambiar una función pura con tests.

Módulos:

- ``routing``    — qué proveedor usa cada stage (Codex vs Claude, sonnet vs opus)
- ``gating``     — debe publicarse el verdict, requiere human review
- ``security``   — necesita el security_pass correr, pasa el scan
- ``budget``     — caben los tokens, dentro del budget de costo
- ``escalation`` — cuándo escalar a HITL por baja confianza o requirements ambiguos

Reglas de pureza:

1. Funciones top-level (no clases con state) — deterministas
2. Solo tipos del dominio como input (Plan, Verdict, contadores enteros)
3. Output: enum, bool, named tuple, o dataclass simple
4. Cero ``import os``, ``import requests``, ``time.sleep``, etc.
"""

from .budget import BudgetCheck, within_cost, within_token_budget
from .escalation import EscalationReason, needs_escalation, wrong_requirements_signal
from .gating import GateResult, requires_human, should_publish
from .routing import select_generator, select_synthesizer
from .security import SecurityGate, gate_security, needs_security_pass

__all__ = [
    "BudgetCheck",
    "EscalationReason",
    "GateResult",
    "SecurityGate",
    "gate_security",
    "needs_escalation",
    "needs_security_pass",
    "requires_human",
    "select_generator",
    "select_synthesizer",
    "should_publish",
    "within_cost",
    "within_token_budget",
    "wrong_requirements_signal",
]

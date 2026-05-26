"""Routing policies — qué proveedor usa cada stage.

Estas funciones encapsulan la tabla del plan v7 sección 7 (ExecutionPlan
routing). NO usan if-elif en N call sites — un solo lugar decide.

Codex SIEMPRE corre con gpt-5.5 (invariante hardcoded en infra/constants.py).
El routing aquí solo afecta a Claude (sonnet vs opus + deep_analysis flag).

`select_generator` y `select_synthesizer` son funciones puras: misma input,
mismo output, sin side effects. Tests usan pytest con tablas de casos.
"""

from __future__ import annotations

from enum import Enum

from ..domain.plan import Complexity, ExecutionPlan


class GeneratorChoice(str, Enum):
    """Proveedor para el codex_stage.

    En el pipeline multi-perspective canónico, Codex es el ÚNICO generator.
    Este enum existe para que F-future pueda agregar variantes (e.g.
    `CODEX_FAST` con un budget reducido) sin romper call sites.
    """

    CODEX_GPT55 = "codex_gpt_5_5"


class SynthesizerChoice(str, Enum):
    """Modelo para el claude_stage. Las tres opciones del plan v7 sección 7."""

    SONNET_NO_DEEP = "sonnet_4_6_no_deep"  # simple
    SONNET_DEEP = "sonnet_4_6_deep"        # medium
    OPUS_DEEP = "opus_4_7_deep"            # complex


def select_generator(plan: ExecutionPlan) -> GeneratorChoice:
    """El classifier NO afecta al generator — Codex siempre gpt-5.5.

    Esta función existe para que el call site sea uniforme con
    `select_synthesizer` y para que F-future pueda añadir capability checks
    (e.g. "este plan requiere tool-use, ¿lo soporta el generator?").
    """
    return GeneratorChoice.CODEX_GPT55


def select_synthesizer(plan: ExecutionPlan) -> SynthesizerChoice:
    """Mapea complexity efectiva → modelo Claude para synthesis.

    ``effective_complexity`` ya aplica el flag ``force_opus``, así que esta
    función solo necesita la tabla simple.
    """
    eff = plan.effective_complexity
    if eff is Complexity.SIMPLE:
        return SynthesizerChoice.SONNET_NO_DEEP
    if eff is Complexity.MEDIUM:
        return SynthesizerChoice.SONNET_DEEP
    if eff is Complexity.COMPLEX:
        return SynthesizerChoice.OPUS_DEEP
    # Imposible — todos los miembros del Enum cubiertos arriba. El raise es
    # defensivo para que el typechecker confirme exhaustividad.
    raise ValueError(f"Unknown complexity: {eff!r}")

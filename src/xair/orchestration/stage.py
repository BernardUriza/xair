"""Stage — unidad de trabajo dentro de un Pipeline.

Un Stage envuelve una función con metadata: nombre, dependencias, política de
fallo. La función (`fn`) recibe:

- `context`: input compartido del pipeline (immutable, viene del caller)
- `prev`: dict de outputs de stages previos `{stage_name: output}`

Y retorna su output, que el Executor guarda y pasa a stages downstream.

Stages son value objects (frozen dataclass). Ejecutarlos no es responsabilidad
del Stage — eso es del Executor. Esta separación permite tener distintos
Executors (sync, async, distributed) consumiendo los mismos Stages.

Side effects son permitidos pero deben declararse en docstring de `fn`. El
Executor no enforza pureza — confiamos en que stages declaren honestamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Literal


# Firma canónica de una función-stage:
#   (context, previous_outputs) -> output
StageFn = Callable[[Any, dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class Stage:
    """Unidad de trabajo en un Pipeline.

    Args:
        name: identificador único dentro del pipeline (no se permite duplicado)
        fn: función a ejecutar (firma StageFn)
        depends_on: nombres de stages que deben completar antes que éste corra
        on_failure: comportamiento si `fn` levanta excepción:
            - "abort": detiene el pipeline; stages downstream se marcan SKIPPED
            - "continue": registra el fallo pero ejecuta stages independientes
                          que no dependen de éste
            - "warn": idéntico a "continue" semánticamente; señala intent de
                      logging a nivel WARN (cuando se integre logging real)
    """

    name: str
    fn: StageFn
    depends_on: tuple[str, ...] = ()
    on_failure: Literal["abort", "continue", "warn"] = "abort"


class StageStatus(str, Enum):
    """Resultado posible de la ejecución de un Stage."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # un upstream con on_failure=abort falló


@dataclass(frozen=True, slots=True)
class StageRecord:
    """Procedence record de una ejecución de Stage.

    Capturado por el Executor y agregado al ExecutionTrace. NO contiene el
    output completo — solo un summary. Los outputs viven en `PipelineResult.outputs`
    porque pueden ser grandes o sensibles (e.g., review markdown, diff).

    Lo que SÍ vive aquí: nombre, status, timestamps, error string, summary.
    Suficiente para auditar qué corrió, en qué orden, con qué outcome.
    """

    stage_name: str
    status: StageStatus
    started_at: datetime
    finished_at: datetime
    output_summary: str = ""
    error: str | None = None

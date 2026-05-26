"""Errores específicos de la capa orchestration.

Jerarquía:
    OrchestrationError                    (cualquier error de la capa)
    ├── PipelineError                     (pipeline mal formado)
    │   ├── CycleError                    (dependencia circular)
    │   └── MissingDependencyError        (depends_on apunta a stage inexistente)
    └── StageError                        (un stage falló en runtime)

Capturar `OrchestrationError` para handler genérico; los hijos para casos
específicos. PipelineError se levanta en construcción del Pipeline (validación
estática); StageError en ejecución.
"""

from __future__ import annotations


class OrchestrationError(Exception):
    """Base de todos los errores de la capa orchestration."""


class StageError(OrchestrationError):
    """Un Stage falló durante ejecución.

    El Executor envuelve la excepción original en `StageError` para registrarla
    en el trace junto con el nombre del stage. La excepción original queda en
    `original` para inspección.
    """

    def __init__(self, stage_name: str, original: Exception) -> None:
        self.stage_name = stage_name
        self.original = original
        super().__init__(f"Stage {stage_name!r} failed: {type(original).__name__}: {original}")


class PipelineError(OrchestrationError):
    """El Pipeline mismo está mal formado (validación estática)."""


class CycleError(PipelineError):
    """Dependencia circular detectada al construir el Pipeline."""


class MissingDependencyError(PipelineError):
    """Un stage declara `depends_on` apuntando a un stage que no está en el pipeline."""

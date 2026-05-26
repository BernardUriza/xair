"""Orchestration — DAG executor, Stage, Pipeline, ExecutionTrace primitives.

Esta capa convierte pipelines de funciones imperativas en grafos declarativos.
Un Pipeline es DATOS (lista de Stages con dependencies); un Executor es CÓDIGO
genérico que consume cualquier Pipeline. Esa separación es el payoff:

- El pipeline se puede inspeccionar, visualizar, validar antes de correr
- Un nuevo pipeline = nuevos datos, no rama nueva en un orquestador
- Distintos Executors (sync, async, distributed) consumen el mismo Pipeline
- Tests de pipelines no necesitan mocks complejos — basta inyectar Stages fake

Ver `frontend/diagrams/xair-multi-perspective.html` secciones 1, 2, 13.
"""

from .exceptions import (
    CycleError,
    MissingDependencyError,
    OrchestrationError,
    PipelineError,
    StageError,
)
from .executor import ExecutionResult, Executor
from .pipeline import Pipeline
from .stage import Stage, StageFn, StageRecord, StageStatus
from .tracing import ExecutionTrace

__all__ = [
    "CycleError",
    "ExecutionResult",
    "ExecutionTrace",
    "Executor",
    "MissingDependencyError",
    "OrchestrationError",
    "Pipeline",
    "PipelineError",
    "Stage",
    "StageError",
    "StageFn",
    "StageRecord",
    "StageStatus",
]

"""ExecutionTrace — audit trail de un Pipeline run.

Requerido por fitness function #3 (Procedence Completeness, plan sección 15).
Cada run produce un Trace; cada Stage contribuye un StageRecord.

Sin esto, debugar pipelines multi-stage es imposible: ¿qué corrió, en qué
orden, qué falló, cuánto duró cada uno? El Trace responde todo.

El Trace es intencionalmente lightweight: nombres + timestamps + status +
summary corto. Outputs completos NO viven aquí — viven en `PipelineResult.outputs`
porque pueden ser grandes o contener data sensible (PII, diffs, prompts).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .stage import StageRecord, StageStatus


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    """Audit record completo de una ejecución de Pipeline."""

    pipeline_name: str
    started_at: datetime
    finished_at: datetime
    records: tuple[StageRecord, ...]

    @property
    def total_stages(self) -> int:
        return len(self.records)

    @property
    def failed_stages(self) -> tuple[StageRecord, ...]:
        return tuple(r for r in self.records if r.status == StageStatus.FAILED)

    @property
    def succeeded_stages(self) -> tuple[StageRecord, ...]:
        return tuple(r for r in self.records if r.status == StageStatus.COMPLETED)

    @property
    def skipped_stages(self) -> tuple[StageRecord, ...]:
        return tuple(r for r in self.records if r.status == StageStatus.SKIPPED)

"""Executor — corre un Pipeline contra un Context, produce outputs y trace.

Esta primera implementación es **secuencial** (topological order, un stage a
la vez). Es deliberadamente simple — paralelismo real (asyncio, threading)
puede sumarse después como un Executor alternativo (`AsyncExecutor`,
`ThreadedExecutor`) sin cambiar el Pipeline.

Política de fallo por stage (ver `Stage.on_failure`):

- "abort": el stage falla → stages downstream que dependen de él se marcan
  SKIPPED. `success=False` en el resultado. Stages independientes corrieron
  igualmente porque ya estaban en orden topológico previo al fallo.
- "continue" / "warn": el stage falla → stages que dependen de él reciben
  SKIPPED, pero `success=True` (porque el caller declaró que el fallo es OK).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..contracts.clock import Clock
from .pipeline import Pipeline
from .stage import StageRecord, StageStatus
from .tracing import ExecutionTrace


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Resultado de correr un Pipeline.

    Attributes:
        outputs: dict {stage_name: output} — un entry por cada stage COMPLETED
        trace: procedence record completo (incluye FAILED y SKIPPED)
        success: True si ningún stage con on_failure="abort" falló
    """

    outputs: dict[str, Any]
    trace: ExecutionTrace
    success: bool


class _SystemClock:
    """Default clock cuando ningún Clock se inyecta. Tests deben inyectar fake."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class Executor:
    """Ejecuta un Pipeline en orden topológico, secuencialmente.

    Args:
        clock: Clock inyectable para tracing reproducible. Si None, usa system clock.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or _SystemClock()

    def run(self, pipeline: Pipeline, context: Any) -> ExecutionResult:
        order = _topological_order(pipeline)
        outputs: dict[str, Any] = {}
        records: list[StageRecord] = []
        # Stages que NO deben ejecutarse porque un upstream con abort falló.
        skipped: set[str] = set()
        aborted = False

        pipeline_started = self._clock.now()

        for stage_name in order:
            stage = pipeline.stage_by_name(stage_name)

            # ¿Algún dep marcado como skipped o failed-with-abort?
            if any(dep in skipped for dep in stage.depends_on):
                ts = self._clock.now()
                records.append(
                    StageRecord(
                        stage_name=stage.name,
                        status=StageStatus.SKIPPED,
                        started_at=ts,
                        finished_at=ts,
                        output_summary="upstream failure",
                    )
                )
                skipped.add(stage.name)
                continue

            started = self._clock.now()
            try:
                output = stage.fn(context, dict(outputs))
            except Exception as exc:
                finished = self._clock.now()
                records.append(
                    StageRecord(
                        stage_name=stage.name,
                        status=StageStatus.FAILED,
                        started_at=started,
                        finished_at=finished,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                if stage.on_failure == "abort":
                    skipped.add(stage.name)
                    aborted = True
                # "continue"/"warn" + exception: downstream stages that
                # depend on this one are SKIPPED because there is no output
                # for them to read. The crashed stage produced nothing.
                # (Soft-failure with continue is different: output IS
                # available, so downstream can run and inspect it.)
                else:
                    skipped.add(stage.name)
                continue

            finished = self._clock.now()
            outputs[stage.name] = output
            records.append(
                StageRecord(
                    stage_name=stage.name,
                    status=StageStatus.COMPLETED,
                    started_at=started,
                    finished_at=finished,
                    output_summary=_summarize(output),
                )
            )

        pipeline_finished = self._clock.now()
        trace = ExecutionTrace(
            pipeline_name=pipeline.name,
            started_at=pipeline_started,
            finished_at=pipeline_finished,
            records=tuple(records),
        )
        return ExecutionResult(outputs=outputs, trace=trace, success=not aborted)


def _topological_order(pipeline: Pipeline) -> list[str]:
    """Kahn's algorithm con orden estable.

    Cuando varios stages tienen indegree=0 al mismo tiempo, escogemos el que
    aparece primero en `pipeline.stages` (orden de declaración). Esto hace que
    runs determinísticos produzcan trazas idénticas — propiedad clave para tests.
    """
    indegree = {s.name: len(s.depends_on) for s in pipeline.stages}
    order_index = {s.name: i for i, s in enumerate(pipeline.stages)}

    rev_deps: dict[str, list[str]] = {s.name: [] for s in pipeline.stages}
    for s in pipeline.stages:
        for dep in s.depends_on:
            rev_deps[dep].append(s.name)

    ready = sorted([n for n, d in indegree.items() if d == 0], key=lambda n: order_index[n])
    result: list[str] = []

    while ready:
        n = ready.pop(0)
        result.append(n)
        for downstream in rev_deps[n]:
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                ready.append(downstream)
                ready.sort(key=lambda name: order_index[name])

    return result


def _summarize(output: Any) -> str:
    """One-line summary del output para el trace.

    Truncamos a 120 chars — suficiente para auditar tipo y forma sin filtrar
    contenido sensible o saturar el log.
    """
    s = repr(output)
    return s if len(s) <= 120 else s[:117] + "..."



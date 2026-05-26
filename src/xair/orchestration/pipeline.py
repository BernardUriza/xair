"""Pipeline — DAG de Stages declarado como datos, no como código.

Un Pipeline es intencionalmente inerte: contiene stages y sus dependencies,
pero NO los ejecuta. Ejecución vive en `Executor`.

La separación es el payoff arquitectónico:

- El pipeline se puede inspeccionar, visualizar, validar antes de correr
- Distintos Executors (sync, async, distributed) consumen el mismo Pipeline
- Agregar un nuevo pipeline = agregar datos, no código nuevo en el orquestador
- Tests construyen pipelines con stages fake — el Executor no cambia

Validación al construir:
- Nombres únicos
- `depends_on` resuelve a stages presentes
- No hay ciclos

Una vez construido, el Pipeline es inmutable. Frozen dataclass garantiza esto.
"""

from __future__ import annotations

from dataclasses import dataclass

from .exceptions import CycleError, MissingDependencyError, PipelineError
from .stage import Stage


@dataclass(frozen=True, slots=True)
class Pipeline:
    """DAG de Stages con un nombre identificativo."""

    name: str
    stages: tuple[Stage, ...]

    def __post_init__(self) -> None:
        self._validate_unique_names()
        self._validate_dependencies()
        self._validate_no_cycles()

    def _validate_unique_names(self) -> None:
        seen: set[str] = set()
        dups: list[str] = []
        for s in self.stages:
            if s.name in seen:
                dups.append(s.name)
            seen.add(s.name)
        if dups:
            raise PipelineError(
                f"Pipeline {self.name!r} has duplicate stage names: {sorted(set(dups))}"
            )

    def _validate_dependencies(self) -> None:
        names = {s.name for s in self.stages}
        for s in self.stages:
            for dep in s.depends_on:
                if dep not in names:
                    raise MissingDependencyError(
                        f"Stage {s.name!r} depends on {dep!r}, "
                        f"which is not in pipeline {self.name!r}"
                    )

    def _validate_no_cycles(self) -> None:
        """DFS-based cycle detection (3-color marking)."""
        graph = {s.name: set(s.depends_on) for s in self.stages}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in graph}

        def visit(node: str) -> None:
            color[node] = GRAY
            for dep in graph[node]:
                if color[dep] == GRAY:
                    raise CycleError(
                        f"Pipeline {self.name!r} has a cycle "
                        f"involving stages {node!r} and {dep!r}"
                    )
                if color[dep] == WHITE:
                    visit(dep)
            color[node] = BLACK

        for n in graph:
            if color[n] == WHITE:
                visit(n)

    def stage_by_name(self, name: str) -> Stage:
        """Lookup O(N). Aceptable porque pipelines son pequeños (<20 stages)."""
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(f"No stage {name!r} in pipeline {self.name!r}")

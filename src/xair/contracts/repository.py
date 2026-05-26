"""Repository — read/write de artefactos persistentes (issues, PRs, learnings).

A diferencia de `FileStore` (key-value local para artefactos de pipeline),
`Repository` describe persistencia de entidades del dominio: issues, PRs,
learnings, runs auditados.

Implementaciones esperadas en `infra/`:
- `MongoRepository` — para learnings y procedence trace
- `GitHubRepository` — para issues y PRs vía gh CLI / REST
- `PlaneRepository` — para issues vía Plane API

Cada Repository concreta una sola entidad. El Protocol genérico aquí es
intencionalmente delgado — los métodos específicos viven en interfaces
más estrechas (IssueRepository, LearningRepository, etc.) que se añadirán
en F4 cuando entren los stages de evidence_gate y claim_decomp.
"""

from __future__ import annotations

from typing import Any, Protocol


class Repository(Protocol):
    """Persistencia de entidades del dominio. Interfaz delgada — los Repositories
    concretos extienden con métodos específicos por tipo de entidad.
    """

    def get(self, identifier: str) -> Any | None: ...

    def save(self, entity: Any) -> str:
        """Persiste y devuelve el identificador asignado."""
        ...

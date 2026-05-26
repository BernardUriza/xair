"""Contracts — Protocols puros, sin dependencias internas al paquete.

Esta capa es la fundación de la arquitectura por capas (ver
`frontend/diagrams/xair-multi-perspective.html` sección 1). Los Protocols
viven aquí porque son contratos estables que cambian con la frecuencia más
baja del sistema. Cualquier callsite que necesite un proveedor (LLM, git,
tracker de issues, etc.) tipa contra estos Protocols, no contra implementaciones
concretas.

Reglas:
- Cero imports de `xair.*` (solo stdlib + typing).
- Una violación es CI failure (ver `tests/test_layer_purity.py`).
- Cambios aquí son major changes — versionar con cuidado.
"""

from .actions_io import ActionsIO
from .agent_runner import AgentRunner
from .clock import Clock
from .file_store import FileStore
from .github import GitHubClient
from .issue_tracker import IssueTrackerClient
from .providers import LlmProvider
from .publisher import Publisher
from .repository import Repository
from .transport import Transport

__all__ = [
    "ActionsIO",
    "AgentRunner",
    "Clock",
    "FileStore",
    "GitHubClient",
    "IssueTrackerClient",
    "LlmProvider",
    "Publisher",
    "Repository",
    "Transport",
]

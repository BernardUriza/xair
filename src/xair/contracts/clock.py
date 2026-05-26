"""Clock — tiempo inyectable.

Existe para que el dominio no llame a `datetime.now()` directamente. Tests
inyectan un `FrozenClock` o `FakeClock`; producción inyecta `SystemClock`
(implementado en `infra/system_clock.py`).

Sin esto, todo procedence trace que incluya `created_at` se vuelve no
determinístico en tests, y los snapshots fallan en orden distinto.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Fuente de tiempo inyectable. Default impl: `infra.system_clock.SystemClock`."""

    def now(self) -> datetime:
        """Hora actual UTC. Tests pueden congelar este valor."""
        ...

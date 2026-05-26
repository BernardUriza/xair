"""AgentRunner — Protocol para invocar un agente LLM con tool-use.

Generaliza la invocación al Claude Agent SDK (production) y a fakes en tests.
La interfaz es mínima — recibe prompt + system + cwd + límites, devuelve
un AgentRunOutcome.

Implementaciones esperadas:

- `infra.agent_runner.ClaudeSDKAgentRunner` — wrapper sobre
  `claude_agent_sdk.query()` con el env var dance para ANTHROPIC_API_KEY
  y el manejo async/asyncio.run del monolito actual de `run_resolve`
- Fakes en tests — devuelven AgentRunOutcome programado sin invocar SDK real

Los stages que necesitan correr un agente tipan contra este Protocol —
zero coupling a una implementación concreta de SDK.
"""

from __future__ import annotations

from typing import Protocol


class AgentRunner(Protocol):
    """Invoca un agente LLM con tool-use y devuelve métricas del run.

    El método ``run`` es síncrono desde el punto de vista del caller — si
    la implementación necesita async internamente (Claude Agent SDK usa
    `query()` async), lo encapsula con `asyncio.run()` u otro mecanismo.

    El caller no necesita saber el modelo, el endpoint, o el dialecto de
    tool-use. Eso vive en la implementación.
    """

    def run(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        cwd: str,
        max_turns: int,
    ) -> object:
        """Invoca al agente. Retorna AgentRunOutcome.

        Return type es `object` aquí (no `AgentRunOutcome`) para evitar
        importar de `domain/` desde `contracts/` — los Protocols son la
        capa más baja, deben ser puros stdlib + typing. El caller hace el
        cast / asume el tipo por convención documentada acá.
        """
        ...

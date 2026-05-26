"""LlmProvider — abstracción sobre cualquier proveedor de chat completion.

Implementaciones en `infra/`: OpenAI, Anthropic Agent SDK, Codex CLI.
"""

from __future__ import annotations

from typing import Protocol


class LlmProvider(Protocol):
    """Llama a un endpoint chat-completion y devuelve JSON parseado."""

    def call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool = True,
    ) -> dict: ...

"""Budget policies — verifica que pipelines no quemen tokens ni dinero.

Dos checks ortogonales:

- ``within_token_budget`` — el contexto + prompt cabe en la ventana del modelo
- ``within_cost`` — el costo estimado del run no excede un techo

Ambos son funciones puras: reciben contadores y umbrales, devuelven bool +
explicación. La obtención de los contadores (token counting, pricing tables)
vive en otras capas — aquí solo decidimos.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BudgetCheck:
    """Resultado de una verificación de presupuesto."""

    within: bool
    used: int          # tokens o cents — depende del check
    limit: int
    reason: str

    @property
    def headroom(self) -> int:
        """Cuánto queda antes de exceder. Negativo si ya se excedió."""
        return self.limit - self.used


def within_token_budget(
    estimated_tokens: int,
    *,
    context_window: int,
    safety_margin: float = 0.9,
) -> BudgetCheck:
    """True si ``estimated_tokens`` cabe en ``context_window * safety_margin``.

    El ``safety_margin`` (default 0.9) deja 10% de cushion para la respuesta
    del modelo. Sin esto, un prompt que consume 100% del context window deja
    cero espacio para el output y produce truncation.
    """
    cap = int(context_window * safety_margin)
    return BudgetCheck(
        within=estimated_tokens <= cap,
        used=estimated_tokens,
        limit=cap,
        reason=(
            f"{estimated_tokens} tokens fits in {cap} cap "
            f"({context_window} window × {safety_margin})"
            if estimated_tokens <= cap
            else f"{estimated_tokens} tokens exceeds {cap} cap"
        ),
    )


def within_cost(
    estimated_cents: int,
    *,
    cap_cents: int,
) -> BudgetCheck:
    """True si el costo estimado del run no excede ``cap_cents``.

    Centavos en lugar de dólares para evitar floating point en pricing tables.
    Un cap de 2500 = $25.00 USD máximo por run.
    """
    return BudgetCheck(
        within=estimated_cents <= cap_cents,
        used=estimated_cents,
        limit=cap_cents,
        reason=(
            f"${estimated_cents/100:.2f} within ${cap_cents/100:.2f} cap"
            if estimated_cents <= cap_cents
            else f"${estimated_cents/100:.2f} exceeds ${cap_cents/100:.2f} cap"
        ),
    )

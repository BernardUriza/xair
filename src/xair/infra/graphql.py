"""Lightweight GraphQL loader — reads .graphql files, caches in memory."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_QUERIES_DIR = Path(__file__).resolve().parent.parent / "queries"


@lru_cache(maxsize=16)
def load_query(name: str) -> str:
    """Load a .graphql file by name (without extension). Cached after first read."""
    path = _QUERIES_DIR / f"{name}.graphql"
    if not path.exists():
        raise FileNotFoundError(f"GraphQL query not found: {path}")
    return path.read_text(encoding="utf-8").strip()

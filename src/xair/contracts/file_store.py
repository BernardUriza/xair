"""FileStore — key-value store para artefactos de pipeline.

Default impl: archivos en `/tmp`. Otros adapters posibles: in-memory para
tests, S3 para artefactos compartidos entre runs.
"""

from __future__ import annotations

from typing import Protocol


class FileStore(Protocol):
    """Key-value store para artefactos de pipeline."""

    def read(self, key: str) -> str: ...

    def write(self, key: str, content: str) -> None: ...

"""FileStore implementations — /tmp I/O and in-memory for tests."""

from __future__ import annotations

from pathlib import Path


class TmpFileStore:
    """Reads/writes files under a root directory (default ``/tmp``)."""

    def __init__(self, root: Path = Path("/tmp")) -> None:
        self._root = root

    def read(self, key: str) -> str:
        path = self._root / key
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write(self, key: str, content: str) -> None:
        (self._root / key).write_text(content, encoding="utf-8")


class InMemoryFileStore:
    """Dict-backed store — deterministic, no filesystem needed."""

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = dict(data) if data else {}

    def read(self, key: str) -> str:
        return self._data.get(key, "")

    def write(self, key: str, content: str) -> None:
        self._data[key] = content

    @property
    def data(self) -> dict[str, str]:
        return self._data

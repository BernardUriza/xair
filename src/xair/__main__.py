"""``python -m xair <command>`` — thin entry point.

All command logic lives in consumer packages that decorate their handlers
with :func:`xair.command_registry.command`. The framework's only job at
this entry point is to call :func:`xair.dispatch.dispatch` with ``argv``.

Consumers should NOT import this module directly — it has no commands
registered. Instead, expose your own ``python -m <consumer> <command>``
entry that registers pipelines at import time and then defers here::

    # in <consumer>/__main__.py
    from . import pipelines  # side-effect: @command registrations
    from xair.dispatch import dispatch
    sys.exit(dispatch(sys.argv[1:]))
"""

from __future__ import annotations

import sys

from .dispatch import dispatch


def main(argv: list[str] | None = None) -> int:
    return dispatch(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

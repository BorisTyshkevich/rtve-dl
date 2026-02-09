from __future__ import annotations

import os
import sys
from datetime import datetime
import time

_DEBUG = False


def set_debug(enabled: bool) -> None:
    global _DEBUG
    _DEBUG = bool(enabled)


def is_debug() -> bool:
    return _DEBUG


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _colorize(level: str, msg: str) -> str:
    if level != "ERROR":
        return msg
    if not sys.stderr.isatty() or os.environ.get("NO_COLOR"):
        return msg
    return f"\x1b[31m{msg}\x1b[0m"


def debug(msg: str) -> None:
    if _DEBUG:
        print(f"[{_ts()}] [DEBUG] {msg}")


def error(msg: str) -> None:
    line = f"[{_ts()}] [ERROR] {msg}"
    print(_colorize("ERROR", line), file=sys.stderr)


class stage:
    """
    Context manager for coarse progress logging.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._t0: float | None = None

    def __enter__(self):
        self._t0 = time.time()
        debug(f"stage:start {self._name}")
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = None if self._t0 is None else (time.time() - self._t0)
        if exc is None:
            debug(f"stage:done {self._name} ({dt:.2f}s)" if dt is not None else f"stage:done {self._name}")
        else:
            error(f"stage:fail {self._name}: {exc}")
        return False

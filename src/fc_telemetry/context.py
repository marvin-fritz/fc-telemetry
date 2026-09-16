"""Job-Kontext: welcher Job läuft gerade, welche laufen prozessweit, letzter Fehler."""

from __future__ import annotations

import threading
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator

_current: ContextVar[str | None] = ContextVar("fc_telemetry_job", default=None)
_lock = threading.Lock()
_active: Counter[str] = Counter()
_last_error: dict | None = None
_errors_total = 0


def current_job() -> str | None:
    return _current.get()


@contextmanager
def job_context(name: str) -> Iterator[None]:
    token = _current.set(name)
    with _lock:
        _active[name] += 1
    try:
        yield
    finally:
        with _lock:
            _active[name] -= 1
            if _active[name] <= 0:
                del _active[name]
        _current.reset(token)


def active_jobs() -> list[str]:
    with _lock:
        return sorted(_active)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record_job_error(name: str, exc: BaseException) -> None:
    global _last_error, _errors_total
    with _lock:
        _errors_total += 1
        _last_error = {
            "job": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "ts": _utc_iso(),
        }


def job_error_state() -> dict:
    with _lock:
        return {"lastError": dict(_last_error) if _last_error else None, "errorsTotal": _errors_total}


def _reset_for_tests() -> None:
    global _last_error, _errors_total
    with _lock:
        _active.clear()
        _last_error = None
        _errors_total = 0

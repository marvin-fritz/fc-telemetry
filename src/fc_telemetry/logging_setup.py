"""Einheitliches Logging: eine JSON-Zeile pro Ereignis, plus Zähler für Warnungen/Fehler."""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from typing import IO

from .context import current_job

# Track the original LogRecord factory (saved only once).
_previous_factory = None
_factory_is_installed = False

# Felder eines LogRecord, die kein "extra" sind.
_STANDARD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
        "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "processName", "process", "message",
        "taskName", "asctime",
    }
)

QUIET_LOGGERS = ("uvicorn.access", "gunicorn.access", "pymongo", "httpx", "httpcore", "urllib3")


def _ts(record: logging.LogRecord) -> str:
    dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        data: dict = {
            "ts": _ts(record),
            "service": self._service,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Prefer job captured at record creation time (via factory in setup_logging); fall back for hand-built records.
        job = getattr(record, 'job', None) or current_job()
        if job:
            data["job"] = job
        extra = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")}
        # Remove job from extra since we handle it separately
        extra.pop("job", None)
        duration = extra.pop("duration_ms", None)
        if duration is not None:
            data["duration_ms"] = duration
        if record.exc_info and record.exc_info[1] is not None:
            data["exc"] = self.formatException(record.exc_info)
        if extra:
            data["extra"] = extra
        return json.dumps(data, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__(fmt=f"%(asctime)s {service} %(levelname)-8s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


class LogCounterHandler(logging.Handler):
    """Zählt WARNING- und ERROR-Records; kein Output."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._lock = threading.Lock()
        self._warnings = 0
        self._errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            if record.levelno >= logging.ERROR:
                self._errors += 1
            else:
                self._warnings += 1

    def snapshot_and_reset(self) -> dict:
        with self._lock:
            snap = {"warnings": self._warnings, "errors": self._errors}
            self._warnings = 0
            self._errors = 0
            return snap


def setup_logging(service: str, level: str = "INFO", fmt: str = "json", stream: IO[str] | None = None) -> LogCounterHandler:
    """Root-Logger auf genau einen Stream-Handler (JSON oder Text) plus Zähler setzen."""
    global _previous_factory, _factory_is_installed

    # Install job-capture factory idempotently.
    if not _factory_is_installed:
        _previous_factory = logging.getLogRecordFactory()

        def _job_capture_factory(name, level, fn, lno, msg, args, exc_info=None, func=None, sinfo=None, **kwargs):
            """LogRecord factory that captures the current job context."""
            record = _previous_factory(name, level, fn, lno, msg, args, exc_info, func, sinfo, **kwargs)
            if not hasattr(record, 'job'):
                job = current_job()
                if job:
                    record.job = job
            return record

        logging.setLogRecordFactory(_job_capture_factory)
        _factory_is_installed = True

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter(service))
    root.addHandler(handler)

    counter = LogCounterHandler()
    root.addHandler(counter)

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return counter


def _reset_for_tests() -> None:
    """Reset logging state for test isolation."""
    global _previous_factory, _factory_is_installed

    root = logging.getLogger()
    # Remove all root handlers.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    # Reset root level to WARNING.
    root.setLevel(logging.WARNING)

    # Restore the previous LogRecord factory if it was saved.
    if _factory_is_installed and _previous_factory:
        logging.setLogRecordFactory(_previous_factory)
    _factory_is_installed = False
    _previous_factory = None

    # Reset QUIET_LOGGERS levels to NOTSET.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)

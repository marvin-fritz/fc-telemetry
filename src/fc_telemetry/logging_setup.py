"""Einheitliches Logging: eine JSON-Zeile pro Ereignis, plus Zähler für Warnungen/Fehler."""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from typing import IO

from .context import current_job


# Install a custom LogRecord factory to capture the current job at record creation time.
_original_log_record_factory = logging.getLogRecordFactory()


def _job_capture_factory(name, level, fn, lno, msg, args, exc_info=None, func=None, sinfo=None, **kwargs):
    """LogRecord factory that captures the current job context."""
    record = _original_log_record_factory(name, level, fn, lno, msg, args, exc_info, func, sinfo, **kwargs)
    if not hasattr(record, 'job'):
        job = current_job()
        if job:
            record.job = job
    return record


logging.setLogRecordFactory(_job_capture_factory)

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
        # Check if job was captured in the record (at creation time), otherwise fall back to current context
        if hasattr(record, 'job'):
            data["job"] = record.job
        else:
            job = current_job()
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
        self._lock2 = threading.Lock()
        self._warnings = 0
        self._errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock2:
            if record.levelno >= logging.ERROR:
                self._errors += 1
            else:
                self._warnings += 1

    def snapshot_and_reset(self) -> dict:
        with self._lock2:
            snap = {"warnings": self._warnings, "errors": self._errors}
            self._warnings = 0
            self._errors = 0
            return snap


def setup_logging(service: str, level: str = "INFO", fmt: str = "json", stream: IO[str] | None = None) -> LogCounterHandler:
    """Root-Logger auf genau einen Stream-Handler (JSON oder Text) plus Zähler setzen."""
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

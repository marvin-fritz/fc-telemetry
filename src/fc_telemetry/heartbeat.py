"""Heartbeat: alle N Sekunden ein Zustandsdokument des Prozesses nach MongoDB."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Protocol

from pymongo import ASCENDING, MongoClient
from pymongo.errors import CollectionInvalid

from .context import active_jobs, job_error_state
from .logging_setup import LogCounterHandler
from .mongo_activity import MongoActivity
from .proc import ProcStats
from .version import detect_version

logger = logging.getLogger("fc_telemetry.heartbeat")

LIVE_COLLECTION = "systemHeartbeats"
HISTORY_COLLECTION = "systemHeartbeatHistory"
HISTORY_CAPPED_BYTES = 20 * 1024 * 1024
LIVE_TTL_SECONDS = 120
WARN_INTERVAL_SECONDS = 60.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HeartbeatWriter(Protocol):
    def ensure_setup(self) -> None: ...
    def write(self, doc: dict, history: bool) -> None: ...


class MongoHeartbeatWriter:
    """Eigener kleiner Client (Pool 1), damit der Heartbeat unabhängig vom Dienst-Client bleibt."""

    def __init__(self, mongo_uri: str, database: str = "financecentre", client: MongoClient | None = None) -> None:
        self._uri = mongo_uri
        self._database = database
        self._client = client

    def _db(self):
        if self._client is None:
            self._client = MongoClient(
                self._uri, maxPoolSize=1, appName="fc-telemetry", serverSelectionTimeoutMS=3000, connectTimeoutMS=3000
            )
        return self._client[self._database]

    def ensure_setup(self) -> None:
        db = self._db()
        db[LIVE_COLLECTION].create_index([("service", ASCENDING), ("pid", ASCENDING)], unique=True)
        db[LIVE_COLLECTION].create_index([("ts", ASCENDING)], expireAfterSeconds=LIVE_TTL_SECONDS)
        try:
            db.create_collection(HISTORY_COLLECTION, capped=True, size=HISTORY_CAPPED_BYTES)
        except CollectionInvalid:
            pass
        db[HISTORY_COLLECTION].create_index([("service", ASCENDING), ("ts", ASCENDING)])

    def write(self, doc: dict, history: bool) -> None:
        db = self._db()
        db[LIVE_COLLECTION].replace_one({"service": doc["service"], "pid": doc["pid"]}, doc, upsert=True)
        if history:
            db[HISTORY_COLLECTION].insert_one(dict(doc))


class Heartbeat:
    def __init__(
        self,
        service: str,
        mongo_uri: str | None = None,
        *,
        writer: HeartbeatWriter | None = None,
        database: str = "financecentre",
        interval: float = 5.0,
        history_every: int = 12,
        activity: MongoActivity | None = None,
        log_counter: LogCounterHandler | None = None,
        providers: dict[str, Callable[[], dict]] | None = None,
        version: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        if writer is None and mongo_uri is None:
            raise ValueError("mongo_uri oder writer angeben")
        self._service = service
        self._writer = writer or MongoHeartbeatWriter(mongo_uri or "", database)
        self._interval = interval
        self._history_every = max(1, history_every)
        self._activity = activity
        self._log_counter = log_counter
        self._providers = providers or {}
        self._version = version or detect_version()
        self._clock = clock
        self._now = now
        self._proc = ProcStats(clock=clock)
        self._started_at = now()
        self._ticks = 0
        self._setup_done = False
        self._last_warn: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def build_document(self) -> dict:
        doc: dict = {
            "service": self._service,
            "ts": self._now(),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "version": self._version,
            "startedAt": self._started_at,
            "intervalSec": self._interval,
            "proc": self._proc.snapshot(),
            "mongo": self._activity.snapshot_and_reset() if self._activity else {"reads": 0, "writes": 0, "errors": 0, "latencyMsAvg": 0.0, "byCollection": {}},
            "jobs": {"current": active_jobs(), **job_error_state()},
            "logs": self._log_counter.snapshot_and_reset() if self._log_counter else {"warnings": 0, "errors": 0},
        }
        for name, provider in self._providers.items():
            try:
                doc[name] = provider()
            except Exception:
                logger.exception("Heartbeat-Provider '%s' fehlgeschlagen", name)
        return doc

    def _warn(self, msg: str, exc: BaseException) -> None:
        now = self._clock()
        if self._last_warn is None or now - self._last_warn >= WARN_INTERVAL_SECONDS:
            self._last_warn = now
            logger.warning("%s: %s: %s", msg, type(exc).__name__, exc)

    def tick(self) -> dict:
        try:
            doc = self.build_document()
            if not self._setup_done:
                self._writer.ensure_setup()
                self._setup_done = True
            history = self._ticks % self._history_every == 0
            self._writer.write(doc, history)
            self._ticks += 1
            return doc
        except Exception as exc:  # Dienst darf nie am Heartbeat scheitern
            self._warn("Heartbeat konnte nicht geschrieben werden", exc)
            return {}

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"fc-heartbeat-{self._service}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

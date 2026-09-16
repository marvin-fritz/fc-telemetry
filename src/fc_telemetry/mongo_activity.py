"""Zählt MongoDB-Kommandos prozessweit über pymongo.monitoring (gilt auch für Motor)."""

from __future__ import annotations

import threading
import time

from pymongo import monitoring

READ_COMMANDS = frozenset({"find", "aggregate", "count", "countDocuments", "distinct", "getMore"})
WRITE_COMMANDS = frozenset({"insert", "update", "delete", "findAndModify", "bulkWrite"})
IGNORED_COLLECTIONS = frozenset({"systemHeartbeats", "systemHeartbeatHistory"})
INFLIGHT_MAX_AGE_SECONDS = 60.0
INFLIGHT_MAX_ENTRIES = 10_000


def _collection_of(event) -> str | None:
    command = event.command or {}
    if event.command_name == "getMore":
        coll = command.get("collection") or command.get("getMore")
    else:
        coll = command.get(event.command_name)
    return coll if isinstance(coll, str) else None


class MongoActivity(monitoring.CommandListener):
    def __init__(self, clock=None) -> None:
        self._lock = threading.Lock()
        self._clock = clock if clock is not None else time.monotonic
        self._inflight: dict[int, tuple[str, str, float]] = {}
        self._reset_locked()

    def _reset_locked(self) -> None:
        self._reads = 0
        self._writes = 0
        self._errors = 0
        self._latency_micros = 0
        self._count = 0
        self._by_collection: dict[str, dict[str, int]] = {}

    def started(self, event) -> None:
        name = event.command_name
        if name in READ_COMMANDS:
            kind = "reads"
        elif name in WRITE_COMMANDS:
            kind = "writes"
        else:
            return
        coll = _collection_of(event)
        if coll is None or coll in IGNORED_COLLECTIONS:
            return
        with self._lock:
            if len(self._inflight) > INFLIGHT_MAX_ENTRIES:
                self._inflight.clear()
            self._inflight[event.request_id] = (kind, coll, self._clock())

    def _finish(self, event, failed: bool) -> None:
        with self._lock:
            entry = self._inflight.pop(event.request_id, None)
            if entry is None:
                return
            kind, coll, _ = entry
            if kind == "reads":
                self._reads += 1
            else:
                self._writes += 1
            if failed:
                self._errors += 1
            self._latency_micros += int(event.duration_micros)
            self._count += 1
            bucket = self._by_collection.get(coll)
            if bucket is None:
                bucket = {"reads": 0, "writes": 0}
                self._by_collection[coll] = bucket
            bucket[kind] += 1

    def succeeded(self, event) -> None:
        self._finish(event, failed=False)

    def failed(self, event) -> None:
        self._finish(event, failed=True)

    def snapshot_and_reset(self) -> dict:
        with self._lock:
            now = self._clock()
            stale_ids = [
                req_id for req_id, (_, _, start_time) in self._inflight.items()
                if now - start_time > INFLIGHT_MAX_AGE_SECONDS
            ]
            for req_id in stale_ids:
                del self._inflight[req_id]
            avg = (self._latency_micros / self._count / 1000.0) if self._count else 0.0
            snap = {
                "reads": self._reads,
                "writes": self._writes,
                "errors": self._errors,
                "latencyMsAvg": round(avg, 2),
                "byCollection": {k: dict(v) for k, v in self._by_collection.items()},
            }
            self._reset_locked()
            return snap


_instance: MongoActivity | None = None
_install_lock = threading.Lock()


def install_mongo_activity() -> MongoActivity:
    """Global registrieren – muss VOR dem Anlegen des ersten MongoClient laufen."""
    global _instance
    with _install_lock:
        if _instance is None:
            _instance = MongoActivity()
            monitoring.register(_instance)
        return _instance

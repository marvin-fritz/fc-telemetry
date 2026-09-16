import logging
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import mongomock
import pytest

from fc_telemetry.context import job_context, record_job_error, _reset_for_tests
from fc_telemetry.heartbeat import Heartbeat, MongoHeartbeatWriter, _merge_counters
from fc_telemetry.logging_setup import LogCounterHandler
from fc_telemetry.mongo_activity import MongoActivity


def _mongo_read(activity, request_id, micros=1000):
    activity.started(
        SimpleNamespace(command_name="find", command={"find": "news"}, request_id=request_id, database_name="financecentre")
    )
    activity.succeeded(SimpleNamespace(command_name="find", request_id=request_id, duration_micros=micros))


@pytest.fixture(autouse=True)
def _reset_context():
    _reset_for_tests()
    yield
    _reset_for_tests()


class ListWriter:
    def __init__(self):
        self.setup_calls = 0
        self.docs = []

    def ensure_setup(self):
        self.setup_calls += 1

    def write(self, doc, history):
        self.docs.append((doc, history))


def _fixed_now():
    return datetime(2026, 9, 16, 19, 20, 5, tzinfo=timezone.utc)


def test_build_document_shape(monkeypatch):
    _reset_for_tests()
    activity = MongoActivity()
    counter = LogCounterHandler()
    counter.emit(logging.LogRecord("x", logging.WARNING, "f", 1, "w", (), None))
    hb = Heartbeat(
        "kraken", writer=ListWriter(), activity=activity, log_counter=counter,
        providers={"http": lambda: {"requests": 3}}, version="abc1234", now=_fixed_now, clock=lambda: 1000.0,
    )
    record_job_error("news", ValueError("x"))
    with job_context("insider_trading"):
        doc = hb.build_document()
    assert doc["service"] == "kraken"
    assert doc["ts"] == _fixed_now()
    assert doc["version"] == "abc1234"
    assert doc["intervalSec"] == 5.0
    assert isinstance(doc["pid"], int) and isinstance(doc["host"], str)
    assert doc["mongo"]["reads"] == 0 and "byCollection" in doc["mongo"]
    assert doc["logs"] == {"warnings": 1, "errors": 0}
    assert doc["http"] == {"requests": 3}
    assert doc["jobs"]["current"] == ["insider_trading"]
    assert doc["jobs"]["errorsTotal"] == 1 and doc["jobs"]["lastError"]["job"] == "news"
    assert "cpuPct" in doc["proc"] and "threads" in doc["proc"]
    assert doc["startedAt"] == _fixed_now()


def test_tick_writes_and_marks_history_every_n(monkeypatch):
    w = ListWriter()
    hb = Heartbeat("aladin", writer=w, history_every=3, now=_fixed_now)
    for _ in range(7):
        hb.tick()
    flags = [h for _, h in w.docs]
    assert flags == [True, False, False, True, False, False, True]
    assert w.setup_calls == 1


def test_provider_failure_does_not_break_document(caplog):
    def boom():
        raise RuntimeError("nein")
    hb = Heartbeat("webapi", writer=ListWriter(), providers={"http": boom}, now=_fixed_now)
    doc = hb.build_document()
    assert "http" not in doc


def test_writer_failure_is_swallowed_and_rate_limited(caplog):
    class Bad(ListWriter):
        def write(self, doc, history):
            raise ConnectionError("mongo weg")
    hb = Heartbeat("webapi", writer=Bad(), now=_fixed_now, clock=lambda: 0.0)
    with caplog.at_level(logging.WARNING):
        hb.tick(); hb.tick()
    assert sum("Heartbeat konnte nicht geschrieben werden" in r.message for r in caplog.records) == 1


def test_failed_write_carries_counters_into_next_tick(caplog):
    class FlakyWriter(ListWriter):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def write(self, doc, history):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("mongo weg")
            super().write(doc, history)

    activity = MongoActivity()
    counter = LogCounterHandler()
    w = FlakyWriter()
    hb = Heartbeat(
        "webapi", writer=w, activity=activity, log_counter=counter,
        providers={"http": lambda: {"requests": 3}}, now=_fixed_now, clock=lambda: 0.0,
    )

    for i in range(2):
        _mongo_read(activity, i)
    counter.emit(logging.LogRecord("x", logging.WARNING, "f", 1, "w", (), None))
    with caplog.at_level(logging.WARNING):
        result1 = hb.tick()
    assert result1 == {}
    assert w.docs == []  # write failed, nothing persisted

    for i in range(2, 5):
        _mongo_read(activity, i)
    counter.emit(logging.LogRecord("x", logging.WARNING, "f", 1, "w", (), None))
    result2 = hb.tick()

    assert len(w.docs) == 1
    written_doc, _ = w.docs[0]
    assert written_doc["mongo"]["reads"] == 5
    assert written_doc["logs"]["warnings"] == 2
    assert written_doc["http"]["requests"] == 6
    assert result2 is written_doc


def test_merge_counters_adds_bycollection():
    pending = {
        "mongo": {
            "reads": 2, "writes": 1, "errors": 0, "latencyMsAvg": 10.0,
            "byCollection": {
                "financecentre.news": {"reads": 2, "writes": 0},
                "financecentre.insiderTrades": {"reads": 0, "writes": 1},
            },
        },
        "logs": {"warnings": 1, "errors": 0},
        "http": {"requests": 3, "latencyMsAvg": 5.0},
    }
    doc = {
        "mongo": {
            "reads": 1, "writes": 0, "errors": 1, "latencyMsAvg": 20.0,
            "byCollection": {"financecentre.news": {"reads": 1, "writes": 0}},
        },
        "logs": {"warnings": 0, "errors": 1},
        "http": {"requests": 4, "latencyMsAvg": 8.0},
    }

    _merge_counters(pending, doc)

    assert doc["mongo"]["reads"] == 3
    assert doc["mongo"]["writes"] == 1
    assert doc["mongo"]["errors"] == 1
    assert doc["mongo"]["byCollection"] == {
        "financecentre.news": {"reads": 3, "writes": 0},
        "financecentre.insiderTrades": {"reads": 0, "writes": 1},
    }
    # latencyMsAvg ist request-count-gewichtet: (10*3 + 20*1) / 4 = 12.5
    assert doc["mongo"]["latencyMsAvg"] == 12.5
    assert doc["logs"] == {"warnings": 1, "errors": 1}
    assert doc["http"]["requests"] == 7
    assert doc["http"]["latencyMsAvg"] == 8.0  # latencyMsAvg-Felder werden nie summiert


def test_build_failure_does_not_kill_tick(caplog, monkeypatch):
    hb = Heartbeat("webapi", writer=ListWriter(), now=_fixed_now, clock=lambda: 0.0)

    def boom():
        raise RuntimeError("kaputt")

    monkeypatch.setattr(hb, "build_document", boom)
    with caplog.at_level(logging.WARNING):
        result1 = hb.tick()
        result2 = hb.tick()
    assert result1 == {}
    assert result2 == {}
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 1


def test_start_stop_thread_runs_ticks():
    w = ListWriter()
    hb = Heartbeat("kraken", writer=w, interval=0.05, now=_fixed_now)
    hb.start()
    time.sleep(0.3)
    hb.stop()
    assert len(w.docs) >= 3
    assert not hb.is_running


def test_mongo_writer_upserts_by_service_and_pid_and_creates_history():
    client = mongomock.MongoClient()
    w = MongoHeartbeatWriter("mongodb://ignored", database="financecentre", client=client)
    doc = {"service": "kraken", "pid": 7, "ts": _fixed_now(), "mongo": {"reads": 1}}
    w.write(doc, history=True)
    w.write({**doc, "mongo": {"reads": 2}}, history=False)
    live = list(client["financecentre"]["systemHeartbeats"].find({}, {"_id": 0}))
    assert len(live) == 1 and live[0]["mongo"]["reads"] == 2
    assert client["financecentre"]["systemHeartbeatHistory"].count_documents({}) == 1


def test_ensure_setup_creates_ttl_index_with_120_seconds():
    calls = []

    class FakeColl:
        def create_index(self, keys, **kwargs):
            calls.append((keys, kwargs))

    class FakeDb(dict):
        def __missing__(self, key):
            self[key] = FakeColl()
            return self[key]

        def create_collection(self, name, **kwargs):
            calls.append(("create_collection", name, kwargs))

    class FakeClient(dict):
        def __missing__(self, key):
            self[key] = FakeDb()
            return self[key]

    w = MongoHeartbeatWriter("mongodb://ignored", client=FakeClient())
    w.ensure_setup()
    assert ([("ts", 1)], {"expireAfterSeconds": 120}) in calls
    assert ("create_collection", "systemHeartbeatHistory", {"capped": True, "size": 20 * 1024 * 1024}) in calls

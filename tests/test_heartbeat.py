import logging
import time
from datetime import datetime, timezone

import mongomock
import pytest

from fc_telemetry.context import job_context, record_job_error, _reset_for_tests
from fc_telemetry.heartbeat import Heartbeat, MongoHeartbeatWriter
from fc_telemetry.logging_setup import LogCounterHandler
from fc_telemetry.mongo_activity import MongoActivity


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
    w.ensure_setup()
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

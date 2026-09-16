from types import SimpleNamespace

import pymongo.monitoring

from fc_telemetry.mongo_activity import MongoActivity, install_mongo_activity


def _started(listener, command_name, coll, request_id):
    event = SimpleNamespace(
        command_name=command_name,
        command={command_name: coll},
        request_id=request_id,
        database_name="financecentre",
    )
    listener.started(event)


def _succeeded(listener, command_name, request_id, micros):
    listener.succeeded(SimpleNamespace(command_name=command_name, request_id=request_id, duration_micros=micros))


def _failed(listener, command_name, request_id, micros):
    listener.failed(SimpleNamespace(command_name=command_name, request_id=request_id, duration_micros=micros))


def test_counts_reads_and_writes_per_collection_with_latency():
    l = MongoActivity()
    _started(l, "find", "insiderTrades", 1); _succeeded(l, "find", 1, 2000)
    _started(l, "update", "insiderTrades", 2); _succeeded(l, "update", 2, 4000)
    _started(l, "insert", "news", 3); _succeeded(l, "insert", 3, 6000)
    _started(l, "getMore", "news", 4); _succeeded(l, "getMore", 4, 8000)
    snap = l.snapshot_and_reset()
    assert snap["reads"] == 2 and snap["writes"] == 2 and snap["errors"] == 0
    assert snap["latencyMsAvg"] == 5.0
    assert snap["byCollection"] == {
        "financecentre.insiderTrades": {"reads": 1, "writes": 1},
        "financecentre.news": {"reads": 1, "writes": 1},
    }
    assert l.snapshot_and_reset()["reads"] == 0


def test_ignores_admin_commands_and_own_collections():
    l = MongoActivity()
    _started(l, "ping", "1", 1); _succeeded(l, "ping", 1, 100)
    _started(l, "listIndexes", "news", 2); _succeeded(l, "listIndexes", 2, 100)
    _started(l, "update", "systemHeartbeats", 3); _succeeded(l, "update", 3, 100)
    snap = l.snapshot_and_reset()
    assert snap == {"reads": 0, "writes": 0, "errors": 0, "latencyMsAvg": 0.0, "byCollection": {}}


def test_failed_commands_count_as_errors():
    l = MongoActivity()
    _started(l, "insert", "news", 1); _failed(l, "insert", 1, 500)
    snap = l.snapshot_and_reset()
    assert snap["errors"] == 1 and snap["writes"] == 1


def test_getmore_uses_collection_from_command():
    l = MongoActivity()
    listener = l
    event = SimpleNamespace(command_name="getMore", command={"getMore": 42, "collection": "stockPrices"}, request_id=9, database_name="financecentre")
    listener.started(event)
    _succeeded(l, "getMore", 9, 100)
    assert l.snapshot_and_reset()["byCollection"] == {"financecentre.stockPrices": {"reads": 1, "writes": 0}}


def test_install_is_idempotent_and_registers_globally():
    a = install_mongo_activity()
    b = install_mongo_activity()
    assert a is b
    assert a in pymongo.monitoring._LISTENERS.command_listeners


def test_stale_inflight_entries_are_evicted_on_snapshot():
    clock_state = {"time": 0.0}

    def mock_clock():
        return clock_state["time"]

    l = MongoActivity(clock=mock_clock)
    _started(l, "find", "news", 1)
    clock_state["time"] = 61.0  # advance clock by 61 seconds
    snap = l.snapshot_and_reset()
    assert snap["reads"] == 0
    assert l._inflight == {}

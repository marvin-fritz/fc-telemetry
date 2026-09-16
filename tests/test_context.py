import threading

import pytest

from fc_telemetry.context import (
    active_jobs,
    current_job,
    job_context,
    job_error_state,
    record_job_error,
    _reset_for_tests,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_current_job_inside_and_outside_context():
    assert current_job() is None
    with job_context("insider_trading"):
        assert current_job() == "insider_trading"
    assert current_job() is None


def test_active_jobs_registry_counts_across_threads():
    started = threading.Event()
    release = threading.Event()

    def worker():
        with job_context("news"):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=worker)
    t.start()
    started.wait(timeout=5)
    with job_context("insider_trading"):
        assert active_jobs() == ["insider_trading", "news"]
    release.set()
    t.join(timeout=5)
    assert active_jobs() == []


def test_nested_same_job_stays_registered_until_last_exit():
    with job_context("a"):
        with job_context("a"):
            assert active_jobs() == ["a"]
        assert active_jobs() == ["a"]
    assert active_jobs() == []


def test_record_job_error_keeps_last_and_counts():
    assert job_error_state() == {"lastError": None, "errorsTotal": 0}
    record_job_error("news", ValueError("kaputt"))
    record_job_error("f13", RuntimeError("auch"))
    state = job_error_state()
    assert state["errorsTotal"] == 2
    assert state["lastError"]["job"] == "f13"
    assert state["lastError"]["type"] == "RuntimeError"
    assert state["lastError"]["message"] == "auch"
    assert state["lastError"]["ts"].endswith("Z")

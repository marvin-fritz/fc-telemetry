import io
import json
import logging

import pytest

from fc_telemetry.context import job_context
from fc_telemetry.logging_setup import JsonFormatter, LogCounterHandler, setup_logging, _reset_for_tests


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging state before and after each test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


def _record(msg="hallo", level=logging.INFO, name="kraken.news", exc_info=None, extra=None):
    logger = logging.getLogger(name)
    record = logger.makeRecord(name, level, "f.py", 1, msg, (), exc_info, extra=extra)
    return record


def test_json_formatter_required_fields_only():
    line = JsonFormatter("kraken").format(_record())
    data = json.loads(line)
    assert data["service"] == "kraken"
    assert data["level"] == "INFO"
    assert data["logger"] == "kraken.news"
    assert data["msg"] == "hallo"
    assert data["ts"].endswith("Z") and "T" in data["ts"]
    assert set(data) == {"ts", "service", "level", "logger", "msg"}


def test_json_formatter_optional_fields():
    setup_logging("kraken", fmt="json", stream=io.StringIO())
    with job_context("news"):
        record = _record(extra={"duration_ms": 12.5, "isin": "DE0007"})
    try:
        raise ValueError("kaputt")
    except ValueError:
        import sys
        record.exc_info = sys.exc_info()
    data = json.loads(JsonFormatter("kraken").format(record))
    assert data["job"] == "news"
    assert data["duration_ms"] == 12.5
    assert data["extra"] == {"isin": "DE0007"}
    assert "ValueError: kaputt" in data["exc"]


def test_setup_logging_installs_single_json_handler_and_counter():
    stream = io.StringIO()
    root = logging.getLogger()
    root.addHandler(logging.StreamHandler(io.StringIO()))  # Altlast, muss weg
    counter = setup_logging("aladin", level="DEBUG", fmt="json", stream=stream)
    assert isinstance(counter, LogCounterHandler)
    stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, LogCounterHandler)]
    assert len(stream_handlers) == 1

    logging.getLogger("aladin.engine").warning("achtung")
    logging.getLogger("aladin.engine").error("fehler")
    logging.getLogger("aladin.engine").info("info")
    lines = [json.loads(l) for l in stream.getvalue().splitlines()]
    assert [l["level"] for l in lines] == ["WARNING", "ERROR", "INFO"]
    assert counter.snapshot_and_reset() == {"warnings": 1, "errors": 1}
    assert counter.snapshot_and_reset() == {"warnings": 0, "errors": 0}


def test_setup_logging_text_format_is_human_readable():
    stream = io.StringIO()
    setup_logging("webapi", level="INFO", fmt="text", stream=stream)
    logging.getLogger("app.x").info("hi")
    assert "INFO" in stream.getvalue() and "app.x" in stream.getvalue() and "hi" in stream.getvalue()


def test_setup_logging_quiets_uvicorn_access():
    setup_logging("webapi", level="INFO", fmt="json", stream=io.StringIO())
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("gunicorn.access").level == logging.WARNING

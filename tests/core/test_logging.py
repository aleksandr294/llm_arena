"""The JSON log formatter: shape, and that it does not filter or alter
hostile content (call sites are responsible for not logging secrets)."""

import json
import logging

import pytest

from components.core.logging import JSONFormatter, configure_logging
from tests.security.helpers import SAMPLE_IDS, SAMPLES


def _make_record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_output_is_json_with_required_fields() -> None:
    record = _make_record("hello", request_id="abc-123")
    line = JSONFormatter().format(record)
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "hello"
    assert payload["request_id"] == "abc-123"
    assert "timestamp" in payload


def test_exception_info_is_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )
    payload = json.loads(JSONFormatter().format(record))
    assert "ValueError" in payload["exc_info"]


@pytest.mark.parametrize("sample_id", SAMPLE_IDS)
def test_hostile_message_is_preserved_not_specially_handled(
    sample_id: str,
) -> None:
    """The formatter must not crash on, strip, or otherwise treat hostile
    text differently from any other message: it is not a sanitizer."""
    sample = SAMPLES[SAMPLE_IDS.index(sample_id)]
    record = _make_record(sample)
    payload = json.loads(JSONFormatter().format(record))
    assert payload["message"] == sample


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("INFO")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONFormatter)

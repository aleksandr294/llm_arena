"""Pure tests of the stored-response bound (no database)."""

import pytest

from components.experiments.runner import (
    TRUNCATION_MARKER,
    ExperimentRunner,
    _bound_response,
)


def test_short_and_exact_limit_text_is_untouched() -> None:
    assert _bound_response("hello", 100) == ("hello", False)
    assert _bound_response("x" * 100, 100) == ("x" * 100, False)


def test_over_limit_text_is_cut_to_the_limit_with_a_visible_marker() -> None:
    text, truncated = _bound_response("x" * 101, 100)
    assert truncated is True
    assert len(text) == 100  # marker included
    assert text.endswith(TRUNCATION_MARKER)
    assert text == "x" * (100 - len(TRUNCATION_MARKER)) + TRUNCATION_MARKER


def test_limit_smaller_than_the_marker_still_holds() -> None:
    text, truncated = _bound_response("x" * 50, 5)
    assert truncated is True
    assert len(text) == 5


def test_nul_bytes_are_removed_before_measuring() -> None:
    assert _bound_response("a\x00b", 2) == ("ab", False)


def test_runner_rejects_a_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="max_response_chars"):
        ExperimentRunner(None, None, max_response_chars=0)  # type: ignore[arg-type]

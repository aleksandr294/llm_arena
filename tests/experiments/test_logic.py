import pytest

from components.experiments.logic import (
    backoff_delay,
    derive_final_status,
    render_prompt,
)
from components.experiments.models import ExperimentStatus


def test_render_replaces_both_tokens() -> None:
    out = render_prompt("Q: {input}\nA: {expected_output}", "2+2", "4")
    assert out == "Q: 2+2\nA: 4"


@pytest.mark.parametrize("text", ["{input}", "{0}", "{}", "{a.b}", "}{"])
def test_render_keeps_braces_in_case_input(text: str) -> None:
    assert render_prompt("x {input} y", text, None) == f"x {text} y"


def test_render_does_not_rescan_case_values() -> None:
    out = render_prompt("{input}|{expected_output}", "{expected_output}", "E")
    assert out == "{expected_output}|E"
    out = render_prompt("{expected_output}|{input}", "I", "{input}")
    assert out == "{input}|I"


def test_render_null_expected_output_is_empty() -> None:
    assert render_prompt("[{expected_output}]{input}", "i", None) == "[]i"


def test_render_other_braces_in_template_are_literal() -> None:
    template = 'Return {"a": 1} for {input} and {unknown} {0}'
    out = render_prompt(template, "x", None)
    assert out == 'Return {"a": 1} for x and {unknown} {0}'


def test_render_repeated_tokens() -> None:
    assert render_prompt("{input}{input}", "a", None) == "aa"


@pytest.mark.parametrize(
    ("counts", "cancel", "expected"),
    [
        ({"succeeded": 3}, False, ExperimentStatus.COMPLETED),
        (
            {"succeeded": 2, "failed": 1},
            False,
            ExperimentStatus.PARTIALLY_FAILED,
        ),
        ({"failed": 3}, False, ExperimentStatus.FAILED),
        ({"succeeded": 1, "cancelled": 2}, True, ExperimentStatus.CANCELLED),
        ({"cancelled": 2}, True, ExperimentStatus.CANCELLED),
        ({"cancelled": 2}, False, ExperimentStatus.CANCELLED),
        ({"succeeded": 1, "failed": 1}, True, ExperimentStatus.CANCELLED),
        ({"succeeded": 2, "pending": 1}, False, None),
        ({"succeeded": 2, "running": 1}, False, None),
        ({"pending": 1}, True, None),
    ],
)
def test_derive_final_status(
    counts: dict[str, int], cancel: bool, expected: ExperimentStatus | None
) -> None:
    assert derive_final_status(counts, cancel) == expected


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_delay(n) for n in range(7)] == [1, 2, 4, 8, 16, 30, 30]


def test_backoff_honors_retry_after_up_to_cap() -> None:
    assert backoff_delay(0, retry_after_seconds=7.5) == 7.5
    assert backoff_delay(0, retry_after_seconds=120) == 30
    assert backoff_delay(3, retry_after_seconds=0) == 0
    assert backoff_delay(0, retry_after_seconds=-5) == 0


def test_backoff_jitter_added_but_total_capped() -> None:
    assert backoff_delay(1, jitter_seconds=0.25) == 2.25
    assert backoff_delay(5, jitter_seconds=3) == 30
    assert backoff_delay(0, retry_after_seconds=5, jitter_seconds=0.5) == 5.5

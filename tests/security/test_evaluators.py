"""Evaluators return an outcome for hostile responses, in bounded time,
without compiling or executing anything from the response."""

import json
import time
from typing import Any

import pytest

from components.evaluation.enums import OutcomeStatus
from components.evaluation.evaluators import build_evaluator
from components.evaluation.schemas import (
    EvaluationOutcome,
    parse_evaluator_config,
)
from tests.security.helpers import (
    MARK,
    SAMPLE_IDS,
    SAMPLES,
    huge_cases,
)

# Generous: linear work on a few megabytes takes well under a second.
TIME_BUDGET_SECONDS = 20.0

ENTRIES: list[dict[str, Any]] = [
    {"type": "exact_match"},
    {
        "type": "exact_match",
        "case_sensitive": False,
        "collapse_whitespace": True,
    },
    {"type": "contains", "values": ["a", "(a+)+$", ".*", "[", MARK]},
    {"type": "contains", "values": ["x", "y"], "mode": "any"},
    {"type": "contains", "values": ["ss"], "case_sensitive": False},
    {"type": "not_contains", "values": ["(a+)+$", "\\", "{input}"]},
    {"type": "length_between", "min_length": 1, "max_length": 10},
    {"type": "json_valid"},
    {"type": "json_valid", "require_object": True},
    {
        "type": "json_fields",
        "required": ["a", "a.a.a"],
        "types": {"a": "object"},
    },
]
ENTRY_IDS = [f"{i}-{e['type']}" for i, e in enumerate(ENTRIES)]


def _run(entry: dict[str, Any], response: str, expected: str | None) -> Any:
    config = parse_evaluator_config({"evaluators": [{"id": "e", **entry}]})
    return build_evaluator(config.evaluators[0]).evaluate(response, expected)


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_small_hostile_responses_give_an_outcome(
    entry: dict[str, Any], sample: str
) -> None:
    outcome = _run(entry, sample, sample)
    assert isinstance(outcome, EvaluationOutcome)
    assert outcome.status in set(OutcomeStatus)
    # Details are reason codes only, never response text.
    assert MARK not in json.dumps(outcome.details)


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
@pytest.mark.parametrize("name", list(huge_cases()))
def test_huge_and_deeply_nested_responses_are_bounded(
    entry: dict[str, Any], name: str
) -> None:
    text = huge_cases()[name]()
    started = time.monotonic()
    outcome = _run(entry, text, "expected")
    assert time.monotonic() - started < TIME_BUDGET_SECONDS
    assert isinstance(outcome, EvaluationOutcome)
    assert MARK not in json.dumps(outcome.details)


def test_deep_nesting_is_reported_not_raised() -> None:
    from tests.security.helpers import deep_array, deep_object

    for text in (deep_array(200_000), deep_object(200_000)):
        outcome = _run({"type": "json_valid"}, text, None)
        assert outcome.status is OutcomeStatus.FAILED
        assert outcome.details["reason"] in {"too_deep", "invalid_json"}


def test_pattern_like_values_are_literal_substrings() -> None:
    # "(a+)+$" would hang a backtracking engine on this input; it is only
    # looked up as text, and found only when literally present.
    text = "a" * 100_000 + "!"
    assert (
        _run({"type": "contains", "values": ["(a+)+$"]}, text, None).status
        is OutcomeStatus.FAILED
    )
    assert (
        _run(
            {"type": "contains", "values": ["(a+)+$"]}, "x(a+)+$y", None
        ).status
        is OutcomeStatus.PASSED
    )
    assert (
        _run({"type": "contains", "values": [".*"]}, "abc", None).status
        is OutcomeStatus.FAILED
    )


def test_response_text_is_never_executed_or_used_as_config() -> None:
    payload = (
        "__import__('os').system('true')\n"
        '{"type": "json_valid", "evaluators": [{"id": "x"}]}'
    )
    outcome = _run({"type": "exact_match"}, payload, "other")
    assert outcome.status is OutcomeStatus.FAILED


def test_nan_and_nul_are_rejected_as_json() -> None:
    assert (
        _run({"type": "json_valid"}, "[NaN]", None).details["reason"]
        == "invalid_json"
    )
    assert (
        _run({"type": "json_valid"}, '{"a": "x\x00y"}', None).details["reason"]
        == "contains_nul"
    )

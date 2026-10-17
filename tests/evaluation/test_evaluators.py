import json
from typing import Any

import pytest

from components.evaluation.evaluators import (
    EVALUATORS,
    BaseEvaluator,
    build_evaluator,
)
from components.evaluation.schemas import (
    EvaluationOutcome,
    parse_evaluator_config,
)

SECRET = "SENTINEL-response-text"


def run(
    entry: dict[str, Any], response: str, expected: str | None = None
) -> EvaluationOutcome:
    config = parse_evaluator_config({"evaluators": [{"id": "x", **entry}]})
    return build_evaluator(config.evaluators[0]).evaluate(response, expected)


def em(**kw: Any) -> dict[str, Any]:
    return {"type": "exact_match", **kw}


def contains(values: list[str], **kw: Any) -> dict[str, Any]:
    return {"type": "contains", "values": values, **kw}


def not_contains(values: list[str], **kw: Any) -> dict[str, Any]:
    return {"type": "not_contains", "values": values, **kw}


def length(**kw: Any) -> dict[str, Any]:
    return {"type": "length_between", **kw}


def jv(**kw: Any) -> dict[str, Any]:
    return {"type": "json_valid", **kw}


def jf(**kw: Any) -> dict[str, Any]:
    return {"type": "json_fields", **kw}


# (entry, response, expected_output, status, reason)
CASES: list[tuple[dict[str, Any], str, str | None, str, str]] = [
    # exact_match
    (em(), " Paris\n", "Paris", "passed", "match"),
    (em(), "Lyon", "Paris", "failed", "mismatch"),
    (em(), "Lyon", None, "skipped", "no_expected_output"),
    (em(), "", "", "passed", "match"),
    (em(trim=False), " Paris", "Paris", "failed", "mismatch"),
    (em(), "paris", "Paris", "failed", "mismatch"),
    (em(case_sensitive=False), "PARIS", "paris", "passed", "match"),
    (em(), "a  b\n c", "a b c", "failed", "mismatch"),
    (em(collapse_whitespace=True), "a  b\n c", "a b c", "passed", "match"),
    (
        em(trim=False, collapse_whitespace=True),
        "  a  b ",
        "a b",
        "passed",
        "match",
    ),
    (em(case_sensitive=False), "STRASSE", "straße", "passed", "match"),
    # contains
    (
        contains(["Paris", "France"]),
        "Paris, France",
        None,
        "passed",
        "all_found",
    ),
    (contains(["Paris", "France"]), "Paris", None, "failed", "missing_values"),
    (contains(["a", "b"], mode="any"), "xbx", None, "passed", "found"),
    (contains(["a", "b"], mode="any"), "xyz", None, "failed", "none_found"),
    (contains(["PARIS"]), "paris", None, "failed", "missing_values"),
    (
        contains(["PARIS"], case_sensitive=False),
        "paris",
        None,
        "passed",
        "all_found",
    ),
    (contains(["a"]), "", None, "failed", "missing_values"),
    # not_contains
    (
        not_contains(["sorry"]),
        "I am sorry",
        None,
        "failed",
        "forbidden_values_found",
    ),
    (not_contains(["sorry"]), "fine", None, "passed", "none_found"),
    (not_contains(["SORRY"]), "sorry", None, "passed", "none_found"),
    (
        not_contains(["SORRY"], case_sensitive=False),
        "sorry",
        None,
        "failed",
        "forbidden_values_found",
    ),
    # length_between
    (length(max_length=10), "x" * 11, None, "failed", "too_long"),
    (length(max_length=10), "x" * 10, None, "passed", "in_range"),
    (length(min_length=3), "xx", None, "failed", "too_short"),
    (length(min_length=3), "xxx", None, "passed", "in_range"),
    (length(min_length=0, max_length=0), "", None, "passed", "in_range"),
    (length(min_length=2, max_length=4), "é😀", None, "passed", "in_range"),
    # json_valid
    (jv(require_object=True), '{"a": 1}', None, "passed", "valid_json"),
    (jv(), "not json", None, "failed", "invalid_json"),
    (jv(), '  \n{"a": 1}\n ', None, "passed", "valid_json"),
    (jv(), "[1, 2]", None, "passed", "valid_json"),
    (jv(require_object=True), "[1]", None, "failed", "not_object"),
    (jv(), '{"a": 1} trailing', None, "failed", "invalid_json"),
    (jv(), "", None, "failed", "invalid_json"),
    (jv(), "NaN", None, "failed", "invalid_json"),
    (jv(), "Infinity", None, "failed", "invalid_json"),
    (jv(), '{"a": "b\x00"}', None, "failed", "contains_nul"),
    (jv(), "[" * 100000, None, "failed", "too_deep"),
    (jv(), "[" * 100000 + "]" * 100000, None, "failed", "too_deep"),
    (jv(), "1" * 10000, None, "failed", "invalid_json"),
    (jv(), '{"a": ' * 100000, None, "failed", "too_deep"),
    # json_fields
    (
        jf(required=["name", "address.city"]),
        '{"name": "a", "address": {"city": "x"}}',
        None,
        "passed",
        "fields_ok",
    ),
    (
        jf(required=["name", "address.city"]),
        '{"name": "a", "address": {}}',
        None,
        "failed",
        "fields_invalid",
    ),
    (
        jf(required=["a"]),
        '{"a": null}',
        None,
        "passed",
        "fields_ok",
    ),
    (jf(required=["a.b"]), '{"a": 5}', None, "failed", "fields_invalid"),
    (jf(required=["a"]), "[1]", None, "failed", "not_object"),
    (jf(required=["a"]), "nope", None, "failed", "invalid_json"),
    (jf(required=["a"]), "[" * 100000, None, "failed", "too_deep"),
    (jf(required=["a"]), "{\x00}", None, "failed", "contains_nul"),
    (
        jf(types={"age": "integer"}),
        '{"age": "7"}',
        None,
        "failed",
        "fields_invalid",
    ),
    (jf(types={"age": "integer"}), '{"age": 7}', None, "passed", "fields_ok"),
    (
        jf(types={"age": "integer"}),
        '{"age": 7.5}',
        None,
        "failed",
        "fields_invalid",
    ),
    (
        jf(types={"age": "integer"}),
        '{"age": true}',
        None,
        "failed",
        "fields_invalid",
    ),
    (
        jf(types={"age": "number"}),
        '{"age": true}',
        None,
        "failed",
        "fields_invalid",
    ),
    (jf(types={"age": "number"}), '{"age": 7.5}', None, "passed", "fields_ok"),
    (jf(types={"age": "number"}), '{"age": 7}', None, "passed", "fields_ok"),
    (jf(types={"ok": "boolean"}), '{"ok": false}', None, "passed", "fields_ok"),
    (
        jf(types={"ok": "boolean"}),
        '{"ok": 0}',
        None,
        "failed",
        "fields_invalid",
    ),
    (jf(types={"a": "array"}), '{"a": []}', None, "passed", "fields_ok"),
    (jf(types={"a": "object"}), '{"a": []}', None, "failed", "fields_invalid"),
    (jf(types={"a": "null"}), '{"a": null}', None, "passed", "fields_ok"),
    (jf(types={"a": "string"}), '{"a": "x"}', None, "passed", "fields_ok"),
    # A type constraint on an absent, non-required field is not a failure.
    (jf(types={"a": "string"}), "{}", None, "passed", "fields_ok"),
]


@pytest.mark.parametrize(
    ("entry", "response", "expected", "status", "reason"),
    CASES,
    ids=[f"{i}-{c[0]['type']}" for i, c in enumerate(CASES)],
)
def test_outcomes(
    entry: dict[str, Any],
    response: str,
    expected: str | None,
    status: str,
    reason: str,
) -> None:
    outcome = run(entry, response, expected)
    assert outcome.status == status
    assert outcome.details["reason"] == reason
    # Details must be storable as JSON.
    json.dumps(outcome.details)


def test_details_list_missing_and_mistyped_paths() -> None:
    outcome = run(
        jf(required=["name", "address.city"], types={"age": "integer"}),
        '{"name": "a", "age": "7"}',
    )
    assert outcome.details == {
        "reason": "fields_invalid",
        "missing": ["address.city"],
        "mistyped": ["age"],
    }


def test_contains_details_list_configured_value_indexes() -> None:
    assert run(contains(["a", "b", "c"]), "b").details == {
        "reason": "missing_values",
        "missing_indexes": [0, 2],
    }
    assert run(not_contains(["a", "b", "c"]), "ac").details == {
        "reason": "forbidden_values_found",
        "found_indexes": [0, 2],
    }


def test_registry_covers_every_type_with_a_version() -> None:
    assert set(EVALUATORS) == {
        "exact_match",
        "contains",
        "not_contains",
        "length_between",
        "json_valid",
        "json_fields",
    }
    for key, cls in EVALUATORS.items():
        assert issubclass(cls, BaseEvaluator)
        assert cls.type == key
        assert isinstance(cls.version, int) and cls.version >= 1


def test_registered_types_match_config_types() -> None:
    entries = [
        em(),
        contains(["a"]),
        not_contains(["a"]),
        length(max_length=1),
        jv(),
        jf(required=["a"]),
    ]
    for i, entry in enumerate(entries):
        parsed = parse_evaluator_config(
            {"evaluators": [{"id": f"e{i}", **entry}]}
        ).evaluators[0]
        evaluator = build_evaluator(parsed)
        assert evaluator.type == entry["type"]


@pytest.mark.parametrize(
    ("entry", "response", "expected"),
    [(c[0], c[1], c[2]) for c in CASES],
)
def test_deterministic(
    entry: dict[str, Any], response: str, expected: str | None
) -> None:
    assert run(entry, response, expected) == run(entry, response, expected)


HOSTILE = [
    SECRET,
    f'{{"{SECRET}": "{SECRET}"}}',
    f"[{SECRET}",
    SECRET + "\x00",
    "[" * 50000 + SECRET,
    json.dumps({"a": SECRET}),
]
ALL_ENTRIES = [
    em(),
    contains(["zzz"]),
    contains(["zzz"], mode="any"),
    not_contains([SECRET[:8]]),
    length(max_length=1),
    jv(),
    jv(require_object=True),
    jf(required=["missing"], types={"a": "integer"}),
]


@pytest.mark.parametrize("entry", ALL_ENTRIES)
@pytest.mark.parametrize("response", HOSTILE)
def test_details_never_contain_response_or_expected_text(
    entry: dict[str, Any], response: str
) -> None:
    outcome = run(entry, response, "SENTINEL-expected")
    dumped = json.dumps(outcome.details)
    assert "SENTINEL" not in dumped
    assert len(dumped) < 500

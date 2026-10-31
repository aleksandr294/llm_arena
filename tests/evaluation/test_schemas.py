from typing import Any

import pytest
from pydantic import ValidationError

from components.evaluation.schemas import (
    EvaluatorConfigSchema,
    normalize_evaluator_config,
    parse_evaluator_config,
)


def _entry(**kw: Any) -> dict[str, Any]:
    return {"id": "e1", "type": "exact_match", **kw}


@pytest.mark.parametrize("raw", [{}, {"evaluators": []}])
def test_empty_configuration_means_no_evaluators(raw: dict[str, Any]) -> None:
    assert parse_evaluator_config(raw).evaluators == []
    assert normalize_evaluator_config(raw) == {"evaluators": []}


@pytest.mark.parametrize(
    "entry",
    [
        _entry(),
        {"id": "c", "type": "contains", "values": ["a"], "mode": "any"},
        {"id": "n", "type": "not_contains", "values": ["a"]},
        {"id": "l", "type": "length_between", "min_length": 0},
        {"id": "l", "type": "length_between", "max_length": 5},
        {"id": "l", "type": "length_between", "min_length": 2, "max_length": 2},
        {"id": "j", "type": "json_valid", "require_object": True},
        {"id": "f", "type": "json_fields", "required": ["a.b"]},
        {"id": "f", "type": "json_fields", "types": {"a": "integer"}},
        _entry(id="x" * 64),
        {
            "id": "j",
            "type": "llm_judge",
            "criteria": "response must be polite",
            "provider": "openai",
            "model": "gpt-4o-mini",
        },
    ],
)
def test_valid_entries(entry: dict[str, Any]) -> None:
    config = parse_evaluator_config({"evaluators": [entry]})
    assert len(config.evaluators) == 1


def test_llm_judge_alongside_a_deterministic_evaluator() -> None:
    config = parse_evaluator_config(
        {
            "evaluators": [
                _entry(),
                {
                    "id": "j",
                    "type": "llm_judge",
                    "criteria": "response must be factually correct",
                    "provider": "anthropic",
                    "model": "claude-test",
                },
            ]
        }
    )
    assert [e.type for e in config.evaluators] == ["exact_match", "llm_judge"]


def test_defaults_are_filled_into_normalized_dump() -> None:
    dump = normalize_evaluator_config({"evaluators": [_entry()]})
    assert dump == {
        "evaluators": [
            {
                "id": "e1",
                "type": "exact_match",
                "trim": True,
                "case_sensitive": True,
                "collapse_whitespace": False,
            }
        ]
    }
    # Idempotent: a stored dump parses back to itself.
    assert normalize_evaluator_config(dump) == dump


def _loc(raw: dict[str, Any]) -> tuple[Any, ...]:
    with pytest.raises(ValidationError) as info:
        parse_evaluator_config(raw)
    return tuple(info.value.errors()[0]["loc"])


@pytest.mark.parametrize(
    ("raw", "loc_prefix"),
    [
        ({"evaluators": [{"id": "a", "type": "nope"}]}, ("evaluators", 0)),
        ({"evaluators": [{"id": "a"}]}, ("evaluators", 0)),
        ({"evaluators": [_entry(), _entry()]}, ("evaluators",)),
        ({"evaluators": [_entry(bogus=1)]}, ("evaluators", 0, "exact_match")),
        ({"evaluators": [_entry(id="")]}, ("evaluators", 0, "exact_match")),
        (
            {"evaluators": [_entry(id="x" * 65)]},
            ("evaluators", 0, "exact_match"),
        ),
        ({"evaluators": [_entry(id="a\x00")]}, ("evaluators", 0)),
        ({"evaluators": [_entry(trim="maybe")]}, ("evaluators", 0)),
        ({"evaluators": "x"}, ("evaluators",)),
        ({"extra": 1}, ("extra",)),
        (
            {"evaluators": [{"id": "c", "type": "contains", "values": []}]},
            ("evaluators", 0, "contains", "values"),
        ),
        (
            {"evaluators": [{"id": "c", "type": "contains"}]},
            ("evaluators", 0, "contains", "values"),
        ),
        (
            {"evaluators": [{"id": "c", "type": "contains", "values": [""]}]},
            ("evaluators", 0, "contains", "values", 0),
        ),
        (
            {
                "evaluators": [
                    {"id": "c", "type": "contains", "values": ["a"] * 21}
                ]
            },
            ("evaluators", 0, "contains", "values"),
        ),
        (
            {
                "evaluators": [
                    {"id": "c", "type": "contains", "values": ["a" * 1001]}
                ]
            },
            ("evaluators", 0, "contains", "values", 0),
        ),
        (
            {
                "evaluators": [
                    {"id": "c", "type": "contains", "values": ["a\x00"]}
                ]
            },
            ("evaluators", 0, "contains", "values", 0),
        ),
        (
            {
                "evaluators": [
                    {
                        "id": "c",
                        "type": "contains",
                        "values": ["a"],
                        "mode": "x",
                    }
                ]
            },
            ("evaluators", 0, "contains", "mode"),
        ),
        (
            {
                "evaluators": [
                    {
                        "id": "c",
                        "type": "not_contains",
                        "values": ["a"],
                        "mode": "any",
                    }
                ]
            },
            ("evaluators", 0, "not_contains", "mode"),
        ),
        (
            {"evaluators": [{"id": "l", "type": "length_between"}]},
            ("evaluators", 0, "length_between"),
        ),
        (
            {
                "evaluators": [
                    {"id": "l", "type": "length_between", "min_length": -1}
                ]
            },
            ("evaluators", 0, "length_between", "min_length"),
        ),
        (
            {
                "evaluators": [
                    {
                        "id": "l",
                        "type": "length_between",
                        "min_length": 5,
                        "max_length": 4,
                    }
                ]
            },
            ("evaluators", 0, "length_between"),
        ),
        (
            {"evaluators": [{"id": "j", "type": "json_fields"}]},
            ("evaluators", 0, "json_fields"),
        ),
        (
            {
                "evaluators": [
                    {"id": "j", "type": "json_fields", "required": ["a..b"]}
                ]
            },
            ("evaluators", 0, "json_fields", "required", 0),
        ),
        (
            {
                "evaluators": [
                    {
                        "id": "j",
                        "type": "json_fields",
                        "types": {"a": "float"},
                    }
                ]
            },
            ("evaluators", 0, "json_fields", "types", "a"),
        ),
        (
            {
                "evaluators": [
                    {"id": "j", "type": "llm_judge", "provider": "openai"}
                ]
            },
            ("evaluators", 0, "llm_judge"),
        ),
        (
            {
                "evaluators": [
                    {
                        "id": "j",
                        "type": "llm_judge",
                        "criteria": "x",
                        "provider": "mistral",
                        "model": "m",
                    }
                ]
            },
            ("evaluators", 0, "llm_judge", "provider"),
        ),
        (
            {
                "evaluators": [
                    {
                        "id": "j",
                        "type": "llm_judge",
                        "criteria": "x" * 2001,
                        "provider": "openai",
                        "model": "m",
                    }
                ]
            },
            ("evaluators", 0, "llm_judge", "criteria"),
        ),
        (
            {
                "evaluators": [
                    {
                        "id": "j",
                        "type": "llm_judge",
                        "criteria": "",
                        "provider": "openai",
                        "model": "m",
                    }
                ]
            },
            ("evaluators", 0, "llm_judge", "criteria"),
        ),
    ],
)
def test_invalid_configuration_reports_field_path(
    raw: dict[str, Any], loc_prefix: tuple[Any, ...]
) -> None:
    assert _loc(raw)[: len(loc_prefix)] == loc_prefix


def test_at_most_twenty_entries() -> None:
    def entries(n: int) -> dict[str, Any]:
        return {"evaluators": [_entry(id=f"e{i}") for i in range(n)]}

    assert len(parse_evaluator_config(entries(20)).evaluators) == 20
    assert _loc(entries(21)) == ("evaluators",)


def test_schema_is_importable_directly() -> None:
    assert EvaluatorConfigSchema().evaluators == []

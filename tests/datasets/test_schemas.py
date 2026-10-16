from typing import Any

import pytest
from pydantic import ValidationError

from components.datasets.schemas import (
    DatasetCreateSchema,
    TestCaseCreateSchema,
)


def test_name_is_trimmed() -> None:
    assert DatasetCreateSchema(name="  qa  ").name == "qa"


def test_description_defaults_to_none() -> None:
    assert DatasetCreateSchema(name="qa").description is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "   \n\t"},
        {"name": "x" * 256},
        {"name": "a\x00b"},
        {"name": "ok", "description": "x" * 2001},
        {"name": "ok", "description": "a\x00"},
    ],
)
def test_invalid_dataset(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DatasetCreateSchema(**payload)


def test_dataset_limits_are_inclusive() -> None:
    DatasetCreateSchema(name="x" * 255, description="y" * 2000)


def test_case_keeps_content_exactly() -> None:
    case = TestCaseCreateSchema(input='  a\n"b"  ', expected_output="")
    assert case.input == '  a\n"b"  '
    assert case.expected_output == ""
    assert case.metadata is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"input": ""},
        {"input": "x" * 50001},
        {"input": "a\x00"},
        {"input": "ok", "expected_output": "x" * 50001},
        {"input": "ok", "expected_output": "a\x00"},
        {"input": "ok", "metadata": [1, 2]},
        {"input": "ok", "metadata": "text"},
        {"input": "ok", "metadata": {"k": "a\x00"}},
        {"input": "ok", "metadata": {"k": ["a\x00"]}},
    ],
)
def test_invalid_case(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TestCaseCreateSchema(**payload)


def test_case_limits_are_inclusive() -> None:
    TestCaseCreateSchema(
        input="x" * 50000, expected_output="y" * 50000, metadata={"a": {"b": 1}}
    )

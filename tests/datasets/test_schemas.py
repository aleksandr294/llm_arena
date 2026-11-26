from typing import Any

import pytest
from pydantic import ValidationError

from components.core.utils import METADATA_MAX_BYTES
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


def _nested(levels: int) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(levels - 1):
        value = {"a": value}
    return value


def _metadata_error(metadata: Any) -> list[Any]:
    with pytest.raises(ValidationError) as info:
        TestCaseCreateSchema(input="x", metadata=metadata)
    return list(info.value.errors())


def test_metadata_depth_boundary() -> None:
    TestCaseCreateSchema(input="x", metadata=_nested(5))
    errors = _metadata_error(_nested(6))
    assert errors[0]["loc"] == ("metadata",)


def test_metadata_depth_counts_lists() -> None:
    TestCaseCreateSchema(input="x", metadata={"a": [[[[1]]]]})
    _metadata_error({"a": [[[[[1]]]]]})


def test_metadata_key_count_boundary() -> None:
    TestCaseCreateSchema(input="x", metadata={f"k{i}": 1 for i in range(100)})
    errors = _metadata_error({f"k{i}": 1 for i in range(101)})
    assert errors[0]["loc"] == ("metadata",)


def test_metadata_keys_are_counted_across_nesting() -> None:
    nested = {f"g{i}": {"a": 1, "b": 2} for i in range(34)}  # 34 + 68 keys
    _metadata_error(nested)


def test_metadata_size_boundary() -> None:
    overhead = len('{"k": ""}')
    exact = {"k": "x" * (METADATA_MAX_BYTES - overhead)}
    TestCaseCreateSchema(input="x", metadata=exact)
    errors = _metadata_error({"k": "x" * (METADATA_MAX_BYTES - overhead + 1)})
    assert errors[0]["loc"] == ("metadata",)


@pytest.mark.parametrize("name", ["deep_object", "deep_mixed", "wide_array"])
def test_hostile_json_metadata_is_a_validation_error(name: str) -> None:
    from tests.security.helpers import huge_cases

    raw = huge_cases()[name]()
    payload = '{"input": "x", "metadata": ' + raw + "}"
    with pytest.raises(ValidationError):
        TestCaseCreateSchema.model_validate_json(payload)


def test_hostile_corpus_text_within_bounds_is_accepted_verbatim() -> None:
    from tests.security.helpers import SAMPLES

    case = TestCaseCreateSchema(
        input="x", metadata={f"k{i}": text for i, text in enumerate(SAMPLES)}
    )
    assert case.metadata is not None
    assert list(case.metadata.values()) == SAMPLES

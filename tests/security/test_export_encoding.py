"""CSV cells are spreadsheet-safe and JSON round-trips hostile text exactly
(encoders only, no database)."""

import csv
import io
import json

import pytest

from components.metrics.export import ExportRow, _csv_row, _json_row, _render
from components.metrics.utils import FORMULA_PREFIXES, neutralize
from tests.security.helpers import (
    CORPUS,
    FORMULAS,
    MARK,
    SAMPLE_IDS,
    SAMPLES,
)
from tests.security.helpers import (
    FORMULA_PREFIXES as EXPECTED_PREFIXES,
)


def _row(
    text: str, model: str = "m1", error_kind: str | None = None
) -> ExportRow:
    return ExportRow(
        case_position=1,
        provider="openai",
        model=model,
        status="succeeded",
        attempts=1,
        latency_ms=1.5,
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        error_kind=error_kind,
        response_truncated=False,
        evaluations={"e": text},
        rendered_prompt=text,
        response_text=text,
    )


def _parse(rows: list[list[str]]) -> list[list[str]]:
    # newline="" is how the csv module expects its input to be opened.
    # The reader's default 128 KiB field cap is a consumer setting, not a
    # property of the export, so it is raised for the multi-megabyte case.
    previous = csv.field_size_limit(100_000_000)
    try:
        return list(csv.reader(io.StringIO(_render(rows), newline="")))
    finally:
        csv.field_size_limit(previous)


def test_the_neutralized_prefixes_cover_the_spec() -> None:
    assert set(EXPECTED_PREFIXES) <= set(FORMULA_PREFIXES)


@pytest.mark.parametrize("prefix", EXPECTED_PREFIXES)
def test_every_formula_prefix_is_neutralized(prefix: str) -> None:
    assert neutralize(prefix + "1+1") == "'" + prefix + "1+1"


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_csv_round_trip_never_yields_a_formula_cell(sample: str) -> None:
    # Hostile text in every text column: response, model and error kind.
    row = _csv_row(_row(sample, model=sample, error_kind=sample), ["e"], True)
    parsed = _parse([["h"] * len(row), row])
    assert len(parsed) == 2
    assert all(len(r) == len(row) for r in parsed)
    cells = parsed[1]
    for cell in cells:
        assert not cell.startswith(EXPECTED_PREFIXES), repr(cell)
    expected = "'" + sample if sample.startswith(EXPECTED_PREFIXES) else sample
    assert cells[-1] == expected
    assert cells[2] == expected  # model
    assert cells[9] == expected  # error kind


def test_formula_samples_are_all_altered_and_marked() -> None:
    for name, sample in FORMULAS.items():
        assert sample.startswith(EXPECTED_PREFIXES), name
        assert neutralize(sample) == "'" + sample


def test_many_hostile_rows_keep_the_csv_shape() -> None:
    rows = [_csv_row(_row(s), ["e"], True) for s in SAMPLES] * 3
    parsed = _parse(rows)
    assert len(parsed) == len(rows)
    assert {len(r) for r in parsed} == {len(rows[0])}


@pytest.mark.parametrize("sample", SAMPLES, ids=SAMPLE_IDS)
def test_json_row_round_trips_exactly(sample: str) -> None:
    item = _json_row(_row(sample), True)
    decoded = json.loads(json.dumps(item))
    assert decoded["response_text"] == sample
    assert decoded["rendered_prompt"] == sample
    assert "response_text" not in _json_row(_row(sample), False)


def test_long_and_odd_lines_survive_both_encoders() -> None:
    text = (MARK + "x" * 3_000_000) + "\n" + "\r".join(CORPUS.values())
    cell = _parse([_csv_row(_row(text), ["e"], True)])[0][-1]
    assert cell == text
    assert (
        json.loads(json.dumps(_json_row(_row(text), True)))["response_text"]
        == text
    )

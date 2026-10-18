"""Pure tests of the rendered prompt size bounds (no database)."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from components.experiments.exceptions import ExperimentValidationError
from components.experiments.service import ExperimentService
from components.experiments.utils import (
    MAX_RENDERED_PROMPT_CHARS,
    MAX_TOTAL_RENDERED_CHARS,
    render_prompt,
    rendered_length,
)


def _case(input_text: str, expected: str | None = None, position: int = 1):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        input=input_text, expected_output=expected, position=position
    )


def _check(template: str, cases: list[Any], targets: int = 1) -> None:
    ExperimentService._check_rendered_sizes(template, cast(Any, cases), targets)


@pytest.mark.parametrize(
    ("template", "input_text", "expected"),
    [
        ("Q: {input}", "abc", None),
        ("{input}{input} {expected_output}", "ab", "xyz"),
        ("no placeholders {x}", "ignored", "ignored"),
        ("{input}", "{expected_output}", "{input}"),
    ],
)
def test_rendered_length_matches_render_prompt(
    template: str, input_text: str, expected: str | None
) -> None:
    assert rendered_length(template, input_text, expected) == len(
        render_prompt(template, input_text, expected)
    )


def test_normal_prompts_are_unaffected() -> None:
    _check("Answer: {input}", [_case("a" * 50_000), _case("b")], targets=3)


def test_maximal_schema_fields_still_fit() -> None:
    # 50k template + 50k input + 50k expected output, each used once.
    template = "t" * 49_000 + "{input}{expected_output}"
    _check(template, [_case("i" * 50_000, "e" * 50_000)])


def test_amplification_is_rejected_before_rendering() -> None:
    # 50k template of "{input}" x 7142 against a 50k input would be 357 MB.
    template = "{input}" * 7_142
    with pytest.raises(ExperimentValidationError) as exc:
        _check(template, [_case("x" * 50_000)])
    assert exc.value.field == "prompt_template"


def test_boundary_exact_limit_passes_one_over_fails() -> None:
    exact = "x" * MAX_RENDERED_PROMPT_CHARS
    _check("{input}", [_case(exact)])
    with pytest.raises(ExperimentValidationError):
        _check("{input}", [_case(exact + "x")])


def test_total_across_cases_and_targets_is_bounded() -> None:
    size = MAX_RENDERED_PROMPT_CHARS
    needed = MAX_TOTAL_RENDERED_CHARS // size + 1
    cases = [_case("x" * size, position=p) for p in range(1, needed + 1)]
    with pytest.raises(ExperimentValidationError) as exc:
        _check("{input}", cases)
    assert "in total" in exc.value.message

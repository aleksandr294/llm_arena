"""Deterministic evaluators.

Each evaluator is a pure function of the response text, the case's expected
output and its parameters. Inputs are untrusted: nothing is executed or
interpreted as a pattern, work is linear in the response length, and
``details`` carry reason codes and configured paths only, never response
text. A change in an evaluator's behavior must bump its ``version``.
"""

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from components.evaluation.enums import OutcomeStatus
from components.evaluation.schemas import (
    ContainsParams,
    EvaluationOutcome,
    EvaluatorEntry,
    ExactMatchParams,
    JsonFieldsParams,
    JsonValidParams,
    LengthBetweenParams,
    NotContainsParams,
)


def _outcome(
    status: OutcomeStatus, reason: str, **details: Any
) -> EvaluationOutcome:
    return EvaluationOutcome(
        status=status, details={"reason": reason, **details}
    )


def _passed(reason: str = "ok", **details: Any) -> EvaluationOutcome:
    return _outcome(OutcomeStatus.PASSED, reason, **details)


def _failed(reason: str, **details: Any) -> EvaluationOutcome:
    return _outcome(OutcomeStatus.FAILED, reason, **details)


class BaseEvaluator[ParamsT: BaseModel](ABC):
    type: ClassVar[str]
    version: ClassVar[int]

    def __init__(self, params: ParamsT) -> None:
        self.params = params

    @abstractmethod
    def evaluate(
        self, response_text: str, expected_output: str | None
    ) -> EvaluationOutcome: ...


class ExactMatchEvaluator(BaseEvaluator[ExactMatchParams]):
    type = "exact_match"
    version = 1

    def _normalize(self, value: str) -> str:
        # Order: trim, collapse whitespace, casefold.
        if self.params.trim:
            value = value.strip()
        if self.params.collapse_whitespace:
            value = " ".join(value.split())
        if not self.params.case_sensitive:
            value = value.casefold()
        return value

    def evaluate(
        self, response_text: str, expected_output: str | None
    ) -> EvaluationOutcome:
        if expected_output is None:
            return _outcome(OutcomeStatus.SKIPPED, "no_expected_output")
        if self._normalize(response_text) == self._normalize(expected_output):
            return _passed("match")
        return _failed("mismatch")


def _find_values(
    text: str, values: list[str], case_sensitive: bool
) -> list[int]:
    """Indexes (into the configured values) of those found in ``text``."""
    if not case_sensitive:
        text = text.casefold()
        values = [v.casefold() for v in values]
    return [i for i, value in enumerate(values) if value in text]


class ContainsEvaluator(BaseEvaluator[ContainsParams]):
    type = "contains"
    version = 1

    def evaluate(
        self, response_text: str, expected_output: str | None
    ) -> EvaluationOutcome:
        found = _find_values(
            response_text, self.params.values, self.params.case_sensitive
        )
        if self.params.mode == "any":
            if found:
                return _passed("found")
            return _failed("none_found")
        present = set(found)
        missing = [
            i for i in range(len(self.params.values)) if i not in present
        ]
        if missing:
            return _failed("missing_values", missing_indexes=missing)
        return _passed("all_found")


class NotContainsEvaluator(BaseEvaluator[NotContainsParams]):
    type = "not_contains"
    version = 1

    def evaluate(
        self, response_text: str, expected_output: str | None
    ) -> EvaluationOutcome:
        found = _find_values(
            response_text, self.params.values, self.params.case_sensitive
        )
        if found:
            return _failed("forbidden_values_found", found_indexes=found)
        return _passed("none_found")


class LengthBetweenEvaluator(BaseEvaluator[LengthBetweenParams]):
    type = "length_between"
    version = 1

    def evaluate(
        self, response_text: str, expected_output: str | None
    ) -> EvaluationOutcome:
        length = len(response_text)
        low, high = self.params.min_length, self.params.max_length
        if low is not None and length < low:
            return _failed("too_short", length=length)
        if high is not None and length > high:
            return _failed("too_long", length=length)
        return _passed("in_range", length=length)


def _reject_constant(name: str) -> Any:
    raise ValueError("non-standard JSON constant")


def _parse_json(text: str) -> tuple[Any, str | None]:
    """Parse the stripped response; returns ``(value, None)`` or
    ``(None, reason)``. Never raises on hostile input."""
    if "\x00" in text:
        return None, "contains_nul"
    try:
        # NaN/Infinity are not JSON; reject them.
        return json.loads(text.strip(), parse_constant=_reject_constant), None
    except RecursionError:
        return None, "too_deep"
    except ValueError:  # includes JSONDecodeError and huge-int limits
        return None, "invalid_json"


class JsonValidEvaluator(BaseEvaluator[JsonValidParams]):
    type = "json_valid"
    version = 1

    def evaluate(
        self, response_text: str, expected_output: str | None
    ) -> EvaluationOutcome:
        value, reason = _parse_json(response_text)
        if reason is not None:
            return _failed(reason)
        if self.params.require_object and not isinstance(value, dict):
            return _failed("not_object")
        return _passed("valid_json")


def _matches_type(value: Any, expected: str) -> bool:
    # bool is a subclass of int in Python but not a JSON number.
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return value is None  # "null"


_MISSING = object()


def _lookup(document: dict[str, Any], path: str) -> Any:
    """Walk object keys only (no array indexing); keys containing a dot
    are unsupported."""
    current: Any = document
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
    return current


class JsonFieldsEvaluator(BaseEvaluator[JsonFieldsParams]):
    type = "json_fields"
    version = 1

    def evaluate(
        self, response_text: str, expected_output: str | None
    ) -> EvaluationOutcome:
        document, reason = _parse_json(response_text)
        if reason is not None:
            return _failed(reason)
        if not isinstance(document, dict):
            return _failed("not_object")
        missing = [
            path
            for path in self.params.required
            if _lookup(document, path) is _MISSING
        ]
        mistyped = []
        for path, expected in self.params.types.items():
            value = _lookup(document, path)
            if value is not _MISSING and not _matches_type(value, expected):
                mistyped.append(path)
        if missing or mistyped:
            return _failed("fields_invalid", missing=missing, mistyped=mistyped)
        return _passed("fields_ok")


EVALUATORS: dict[str, type[BaseEvaluator[Any]]] = {
    cls.type: cls
    for cls in (
        ExactMatchEvaluator,
        ContainsEvaluator,
        NotContainsEvaluator,
        LengthBetweenEvaluator,
        JsonValidEvaluator,
        JsonFieldsEvaluator,
    )
}


def build_evaluator(entry: EvaluatorEntry) -> BaseEvaluator[Any]:
    """Instantiate the evaluator for a validated configuration entry; the
    entry itself carries the parameters."""
    return EVALUATORS[entry.type](entry)

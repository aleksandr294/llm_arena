import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from components.datasets.schemas import _reject_nul

MAX_EVALUATORS = 20
MAX_VALUES = 20
VALUE_MAX = 1000
MAX_FIELD_PATHS = 50
PATH_MAX = 200

JsonType = Literal[
    "string", "number", "integer", "boolean", "array", "object", "null"
]


class OutcomeStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class EvaluationOutcome(BaseModel):
    """Result of one evaluator. ``details`` holds reason codes and
    configured field paths only, never response text."""

    model_config = ConfigDict(frozen=True)

    status: OutcomeStatus
    details: dict[str, Any] = Field(default_factory=dict)


def _valid_path(value: str) -> str:
    _reject_nul(value)
    if any(not part for part in value.split(".")):
        raise ValueError("must be dot-separated non-empty keys")
    return value


EvaluatorIdStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64),
    AfterValidator(_reject_nul),
]
ValueStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=VALUE_MAX),
    AfterValidator(_reject_nul),
]
FieldPathStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=PATH_MAX),
    AfterValidator(_valid_path),
]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExactMatchParams(_Params):
    trim: bool = True
    case_sensitive: bool = True
    collapse_whitespace: bool = False


class ContainsParams(_Params):
    values: list[ValueStr] = Field(min_length=1, max_length=MAX_VALUES)
    mode: Literal["all", "any"] = "all"
    case_sensitive: bool = True


class NotContainsParams(_Params):
    values: list[ValueStr] = Field(min_length=1, max_length=MAX_VALUES)
    case_sensitive: bool = True


class LengthBetweenParams(_Params):
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> "LengthBetweenParams":
        if self.min_length is None and self.max_length is None:
            raise ValueError("min_length or max_length is required")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length must not exceed max_length")
        return self


class JsonValidParams(_Params):
    require_object: bool = False


class JsonFieldsParams(_Params):
    required: list[FieldPathStr] = Field(
        default_factory=list, max_length=MAX_FIELD_PATHS
    )
    types: dict[FieldPathStr, JsonType] = Field(
        default_factory=dict, max_length=MAX_FIELD_PATHS
    )

    @model_validator(mode="after")
    def _check_not_empty(self) -> "JsonFieldsParams":
        if not self.required and not self.types:
            raise ValueError("required or types must not be empty")
        return self


class _Entry(_Params):
    id: EvaluatorIdStr


class ExactMatchEntry(_Entry, ExactMatchParams):
    type: Literal["exact_match"]


class ContainsEntry(_Entry, ContainsParams):
    type: Literal["contains"]


class NotContainsEntry(_Entry, NotContainsParams):
    type: Literal["not_contains"]


class LengthBetweenEntry(_Entry, LengthBetweenParams):
    type: Literal["length_between"]


class JsonValidEntry(_Entry, JsonValidParams):
    type: Literal["json_valid"]


class JsonFieldsEntry(_Entry, JsonFieldsParams):
    type: Literal["json_fields"]


EvaluatorEntry = Annotated[
    ExactMatchEntry
    | ContainsEntry
    | NotContainsEntry
    | LengthBetweenEntry
    | JsonValidEntry
    | JsonFieldsEntry,
    Field(discriminator="type"),
]


class EvaluatorConfigSchema(BaseModel):
    """An experiment's evaluator configuration. ``{}`` means no evaluators."""

    model_config = ConfigDict(extra="forbid")

    evaluators: list[EvaluatorEntry] = Field(
        default_factory=list, max_length=MAX_EVALUATORS
    )

    @field_validator("evaluators")
    @classmethod
    def _unique_ids(cls, entries: list[Any]) -> list[Any]:
        seen: set[str] = set()
        for entry in entries:
            if entry.id in seen:
                raise ValueError(f"duplicate evaluator id {entry.id!r}")
            seen.add(entry.id)
        return entries


def parse_evaluator_config(raw: dict[str, Any]) -> EvaluatorConfigSchema:
    """Validate a raw configuration; raises ``ValidationError``."""
    return EvaluatorConfigSchema.model_validate(raw)


def normalize_evaluator_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and return the JSON dump with defaults filled in, which is
    what an experiment stores so defaults are frozen with it."""
    return parse_evaluator_config(raw).model_dump(mode="json")


class EvaluatorSummarySchema(BaseModel):
    """Outcome counts of one configured evaluator; zeros are explicit."""

    id: str
    type: str
    version: int
    passed: int
    failed: int
    skipped: int
    error: int


class EvaluationSummarySchema(BaseModel):
    evaluators: list[EvaluatorSummarySchema]
    # Succeeded results still lacking at least one configured evaluation.
    unevaluated_results: int


class EvaluationResultSchema(BaseModel):
    """One stored outcome. Deliberately has no response text."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    experiment_result_id: uuid.UUID
    evaluator_id: str
    evaluator_type: str
    evaluator_version: int
    status: OutcomeStatus
    details: dict[str, Any]
    created_at: datetime


class EvaluationPageSchema(BaseModel):
    items: list[EvaluationResultSchema]
    total: int
    limit: int
    offset: int


class EvaluationEnqueueSchema(BaseModel):
    experiment_id: uuid.UUID
    # Tasks submitted, not evaluations completed.
    enqueued: int
    # Results still lacking an evaluation after this batch; call again.
    remaining: int

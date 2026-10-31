import uuid
from dataclasses import dataclass
from datetime import datetime
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

from components.core.utils import reject_nul
from components.evaluation.enums import OutcomeStatus
from components.third_party.llm.enums import ProviderName

MAX_EVALUATORS = 20
MAX_VALUES = 20
VALUE_MAX = 1000
MAX_FIELD_PATHS = 50
PATH_MAX = 200
CRITERIA_MAX = 2000

JsonType = Literal[
    "string", "number", "integer", "boolean", "array", "object", "null"
]


class EvaluationOutcome(BaseModel):
    """Result of one evaluator. ``details`` holds reason codes and
    configured field paths only, never response text."""

    model_config = ConfigDict(frozen=True)

    status: OutcomeStatus
    details: dict[str, Any] = Field(default_factory=dict)


def _valid_path(value: str) -> str:
    reject_nul(value)
    if any(not part for part in value.split(".")):
        raise ValueError("must be dot-separated non-empty keys")
    return value


EvaluatorIdStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64),
    AfterValidator(reject_nul),
]
ValueStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=VALUE_MAX),
    AfterValidator(reject_nul),
]
CriteriaStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=CRITERIA_MAX),
    AfterValidator(reject_nul),
]
JudgeModelStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200),
    AfterValidator(reject_nul),
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


class LlmJudgeParams(_Params):
    """A judge call is independent of the experiment's own target models:
    its provider and model are set here, not inherited."""

    criteria: CriteriaStr
    provider: ProviderName
    model: JudgeModelStr


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


class LlmJudgeEntry(_Entry, LlmJudgeParams):
    type: Literal["llm_judge"]


EvaluatorEntry = Annotated[
    ExactMatchEntry
    | ContainsEntry
    | NotContainsEntry
    | LengthBetweenEntry
    | JsonValidEntry
    | JsonFieldsEntry
    | LlmJudgeEntry,
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


@dataclass(frozen=True)
class EvaluationInput:
    """Everything the evaluate task needs, read in one query. ``case_input``
    is read for the llm_judge evaluator only; deterministic evaluators
    ignore it."""

    result_id: uuid.UUID
    experiment_id: uuid.UUID
    status: str
    response_text: str | None
    case_input: str
    expected_output: str | None
    evaluator_config: dict[str, Any]

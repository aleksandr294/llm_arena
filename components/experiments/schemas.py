import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from components.datasets.schemas import (
    TEXT_MAX,
    NameStr,
    _reject_nul,
    _reject_nul_deep,
)
from components.experiments.logic import INPUT_TOKEN
from components.third_party.llm.schemas import ModelTargetSchema

MAX_TARGETS = 10
MAX_ATTEMPTS = 5
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 120.0
EVALUATOR_CONFIG_MAX_BYTES = 10 * 1024


def _require_input_token(value: str) -> str:
    if INPUT_TOKEN not in value:
        raise ValueError(f"must contain the {INPUT_TOKEN} placeholder")
    return value


def _limit_evaluator_size(value: dict[str, Any]) -> dict[str, Any]:
    size = len(json.dumps(value, ensure_ascii=False).encode())
    if size > EVALUATOR_CONFIG_MAX_BYTES:
        raise ValueError(
            f"must be at most {EVALUATOR_CONFIG_MAX_BYTES} bytes serialized"
        )
    return value


PromptTemplateStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=TEXT_MAX),
    AfterValidator(_reject_nul),
    AfterValidator(_require_input_token),
]
SystemPromptStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=TEXT_MAX),
    AfterValidator(_reject_nul),
]
EvaluatorConfigDict = Annotated[
    dict[str, Any],
    AfterValidator(_reject_nul_deep),
    AfterValidator(_limit_evaluator_size),
]


class ExperimentConfigSchema(BaseModel):
    """Frozen run configuration. Limits mirror ``LLMRequestSchema``."""

    targets: list[ModelTargetSchema] = Field(
        min_length=1, max_length=MAX_TARGETS
    )
    system_prompt: SystemPromptStr | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS
    )
    max_attempts: int = Field(
        default=DEFAULT_MAX_ATTEMPTS, ge=1, le=MAX_ATTEMPTS
    )

    @model_validator(mode="after")
    def _distinct_targets(self) -> "ExperimentConfigSchema":
        pairs = [(t.provider, t.model) for t in self.targets]
        if len(set(pairs)) != len(pairs):
            raise ValueError("targets must be distinct")
        return self


class ExperimentCreateSchema(BaseModel):
    name: NameStr
    dataset_id: uuid.UUID
    prompt_template: PromptTemplateStr
    config: ExperimentConfigSchema
    evaluator_config: EvaluatorConfigDict = Field(default_factory=dict)


class ExperimentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    dataset_id: uuid.UUID
    status: str
    prompt_template: str
    config: ExperimentConfigSchema
    evaluator_config: dict[str, Any]
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExperimentDetailSchema(ExperimentSchema):
    # Result count per status; statuses with no results may be absent.
    status_counts: dict[str, int]


class ExperimentResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    experiment_id: uuid.UUID
    test_case_id: uuid.UUID
    case_position: int
    provider: str
    model: str
    status: str
    rendered_prompt: str
    attempts: int
    response_text: str | None
    finish_reason: str | None
    provider_request_id: str | None
    reported_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: float | None
    effective_max_tokens: int | None
    error_kind: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ExperimentPageSchema(BaseModel):
    items: list[ExperimentSchema]
    total: int
    limit: int
    offset: int


class ExperimentResultPageSchema(BaseModel):
    items: list[ExperimentResultSchema]
    total: int
    limit: int
    offset: int


class ExperimentEnqueueSchema(BaseModel):
    """Response of start and retry-failed. ``enqueued`` counts submitted
    tasks, not completed results."""

    experiment: ExperimentSchema
    enqueued: int

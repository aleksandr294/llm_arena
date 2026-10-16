from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ProviderName(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    SERVER_ERROR = "server_error"
    MALFORMED_RESPONSE = "malformed_response"
    UNKNOWN = "unknown"


class ModelTargetSchema(BaseModel):
    """Provider and model chosen by the caller's run configuration."""

    model_config = ConfigDict(frozen=True)

    provider: ProviderName
    model: str = Field(min_length=1)


class LLMRequestSchema(BaseModel):
    """Single-turn text request. Omitted optional values are not sent, so
    provider defaults apply."""

    model_config = ConfigDict(frozen=True)

    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0)


class LLMUsageSchema(BaseModel):
    """Token counts as reported by the provider; ``None`` means unknown."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class LLMResponseSchema(BaseModel):
    text: str
    provider: ProviderName
    requested_model: str
    reported_model: str | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None
    usage: LLMUsageSchema = Field(default_factory=LLMUsageSchema)
    # Value actually sent to the provider; None when none was sent.
    effective_max_tokens: int | None = None
    # Measured by the platform around the SDK call.
    latency_ms: float = Field(ge=0)

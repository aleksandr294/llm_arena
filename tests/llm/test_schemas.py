from typing import Any

import pytest
from pydantic import ValidationError

from components.third_party.llm.schemas import (
    LLMRequestSchema,
    LLMResponseSchema,
    LLMUsageSchema,
    ProviderName,
)


def make(**kw: Any) -> LLMRequestSchema:
    return LLMRequestSchema(**{"model": "m", "prompt": "hi", **kw})


def test_valid_request_defaults_omit_optionals() -> None:
    req = make()
    assert req.temperature is None
    assert req.max_tokens is None
    assert req.system_prompt is None


@pytest.mark.parametrize(
    "kw",
    [
        {"prompt": ""},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"max_tokens": 0},
        {"max_tokens": -5},
        {"temperature": -0.1},
        {"temperature": 2.1},
    ],
)
def test_invalid_request_rejected(kw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make(**kw)


@pytest.mark.parametrize("t", [0, 2])
def test_temperature_bounds_accepted(t: float) -> None:
    assert make(temperature=t).temperature == t


def test_usage_defaults_to_null_not_zero() -> None:
    usage = LLMUsageSchema()
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None
    resp = LLMResponseSchema(
        text="x",
        provider=ProviderName.OPENAI,
        requested_model="m",
        latency_ms=1.0,
    )
    assert resp.usage.total_tokens is None
    assert resp.provider_request_id is None
    assert resp.effective_max_tokens is None

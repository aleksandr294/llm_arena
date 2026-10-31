"""Model-call metrics recorded by ``ExperimentRunner._record_model_metrics``.
Pure unit tests: no database, no provider call — a mocked response is fed
directly to the private recorder."""

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from components.experiments.model_metrics import (
    MODEL_CALL_COST_ESTIMATE_TOTAL,
    MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND,
    MODEL_CALL_LATENCY_SECONDS,
)
from components.experiments.runner import ExperimentRunner
from components.third_party.llm.enums import ProviderName
from components.third_party.llm.schemas import (
    LLMResponseSchema,
    LLMUsageSchema,
    ModelTargetSchema,
)
from tests.security.helpers import PROMPT_ATTACKS


def _runner(pricing_file: str | None = None) -> ExperimentRunner:
    async def _unused_scope() -> None:  # pragma: no cover - never called
        raise AssertionError("scope must not be used by metrics recording")

    return ExperimentRunner(
        _unused_scope,  # type: ignore[arg-type]
        llm_service=None,  # type: ignore[arg-type]
        pricing_file=pricing_file,
    )


def _target(model: str = "gpt-test") -> ModelTargetSchema:
    return ModelTargetSchema(provider=ProviderName.OPENAI, model=model)


def _response(
    *,
    output_tokens: int | None = 50,
    input_tokens: int | None = 10,
    latency_ms: float = 500.0,
    text: str = "hello",
) -> LLMResponseSchema:
    return LLMResponseSchema(
        text=text,
        provider=ProviderName.OPENAI,
        requested_model="gpt-test",
        usage=LLMUsageSchema(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=(
                None
                if input_tokens is None or output_tokens is None
                else input_tokens + output_tokens
            ),
        ),
        latency_ms=latency_ms,
    )


def _histogram_observations(hist: Any, **labels: str) -> float:
    child = hist.labels(**labels)
    return float(sum(bucket.get() for bucket in child._buckets))


def test_latency_and_generation_speed_are_recorded() -> None:
    runner = _runner()
    model = f"gpt-latency-{uuid.uuid4()}"
    before_latency = _histogram_observations(
        MODEL_CALL_LATENCY_SECONDS, provider="openai", model=model
    )
    before_speed = _histogram_observations(
        MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND,
        provider="openai",
        model=model,
    )
    runner._record_model_metrics(
        _target(model), _response(output_tokens=50, latency_ms=500.0)
    )
    assert (
        _histogram_observations(
            MODEL_CALL_LATENCY_SECONDS, provider="openai", model=model
        )
        == before_latency + 1
    )
    assert (
        _histogram_observations(
            MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND,
            provider="openai",
            model=model,
        )
        == before_speed + 1
    )


def test_generation_speed_omitted_when_usage_missing() -> None:
    runner = _runner()
    model = f"gpt-nousage-{uuid.uuid4()}"
    before = _histogram_observations(
        MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND,
        provider="openai",
        model=model,
    )
    runner._record_model_metrics(_target(model), _response(output_tokens=None))
    assert (
        _histogram_observations(
            MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND,
            provider="openai",
            model=model,
        )
        == before
    )


def test_cost_recorded_when_pricing_configured(tmp_path: Path) -> None:
    model = f"gpt-priced-{uuid.uuid4()}"
    pricing_path = tmp_path / "pricing.json"
    pricing_path.write_text(
        json.dumps(
            {
                "version": "1",
                "currency": "USD",
                "rates": {
                    "openai": {
                        model: {
                            "input_per_million": "1",
                            "output_per_million": "2",
                        }
                    }
                },
            }
        )
    )
    runner = _runner(pricing_file=str(pricing_path))
    before = MODEL_CALL_COST_ESTIMATE_TOTAL.labels(
        provider="openai", model=model, currency="USD"
    )._value.get()
    runner._record_model_metrics(
        _target(model),
        _response(input_tokens=1_000_000, output_tokens=1_000_000),
    )
    after = MODEL_CALL_COST_ESTIMATE_TOTAL.labels(
        provider="openai", model=model, currency="USD"
    )._value.get()
    assert Decimal(str(after - before)) == Decimal("3")


def test_cost_omitted_when_pricing_unset() -> None:
    runner = _runner(pricing_file=None)
    model = f"gpt-unpriced-{uuid.uuid4()}"
    before = MODEL_CALL_COST_ESTIMATE_TOTAL.labels(
        provider="openai", model=model, currency="USD"
    )._value.get()
    runner._record_model_metrics(_target(model), _response())
    after = MODEL_CALL_COST_ESTIMATE_TOTAL.labels(
        provider="openai", model=model, currency="USD"
    )._value.get()
    assert after == before


def test_recording_failure_is_swallowed_not_raised() -> None:
    """A metrics problem must never affect generation: a broken recorder
    must not raise out of _record_model_metrics."""
    runner = _runner()
    with patch(
        "components.experiments.runner.record_latency",
        side_effect=RuntimeError("boom"),
    ):
        runner._record_model_metrics(_target(), _response())  # must not raise


@pytest.mark.parametrize("sample_id", list(PROMPT_ATTACKS))
def test_no_hostile_content_reaches_any_metric(sample_id: str) -> None:
    """A hostile prompt never becomes a model/provider label: labels are
    fixed to the configured provider and model, never derived from text."""
    runner = _runner()
    sample = PROMPT_ATTACKS[sample_id]
    # The hostile text is the *response* text; it must never end up in a
    # label or anywhere the Prometheus registry can expose it.
    runner._record_model_metrics(_target("gpt-hostile"), _response(text=sample))
    for metric in (
        MODEL_CALL_LATENCY_SECONDS,
        MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND,
    ):
        for family in metric.collect():
            for sample_metric in family.samples:
                for label_value in sample_metric.labels.values():
                    assert sample not in label_value

"""Opt-in tests that call the real provider APIs (cost money).

Run with RUN_REAL_PROVIDER_TESTS=1 and the relevant API key in the
environment; otherwise they are skipped.
"""

import os

import pytest

from components.core.config import SettingsSchema
from components.third_party.llm.schemas import ModelTargetSchema, ProviderName
from components.third_party.llm.service import LLMService

pytestmark = pytest.mark.real_provider

ENABLED = os.environ.get("RUN_REAL_PROVIDER_TESTS") == "1"

CASES = [
    (ProviderName.OPENAI, "OPENAI_API_KEY", "OPENAI_TEST_MODEL", "gpt-4o-mini"),
    (
        ProviderName.ANTHROPIC,
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TEST_MODEL",
        "claude-haiku-4-5",
    ),
]


@pytest.mark.parametrize(
    ("provider", "key_var", "model_var", "default_model"), CASES
)
async def test_real_roundtrip(
    provider: ProviderName,
    key_var: str,
    model_var: str,
    default_model: str,
) -> None:
    if not ENABLED:
        pytest.skip("set RUN_REAL_PROVIDER_TESTS=1 to run")
    if not os.environ.get(key_var, "").strip():
        pytest.skip(f"{key_var} is not set")
    settings = SettingsSchema(
        database_url="unused",
        rabbitmq_url="unused",
    )
    target = ModelTargetSchema(
        provider=provider, model=os.environ.get(model_var, default_model)
    )
    result = await LLMService(settings).generate(
        target, "Reply with the single word: ok", max_tokens=20
    )
    assert result.text.strip()
    assert result.latency_ms > 0

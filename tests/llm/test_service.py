from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from components.core.config import SettingsSchema
from components.third_party.llm import service as service_module
from components.third_party.llm.schemas import (
    LLMResponseSchema,
    ModelTargetSchema,
    ProviderName,
)
from components.third_party.llm.service import (
    LLMService,
    ProviderNotConfiguredError,
    UnsupportedProviderError,
)


def _settings(**overrides: Any) -> SettingsSchema:
    values: dict[str, Any] = {
        "database_url": "postgresql://x",
        "rabbitmq_url": "amqp://x",
        "openai_api_key": "sk-openai",
        "anthropic_api_key": "sk-anthropic",
        **overrides,
    }
    return SettingsSchema(_env_file=None, **values)


class FakeProvider:
    def __init__(self, name: ProviderName, key: str) -> None:
        self.name = name
        self.key = key
        self.calls: list[Any] = []

    async def generate(self, request: Any) -> LLMResponseSchema:
        self.calls.append(request)
        return LLMResponseSchema(
            text="ok",
            provider=self.name,
            requested_model=request.model,
            latency_ms=1.0,
        )


@pytest.fixture
def factories(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace adapter factories; the mock records every creation."""
    created = MagicMock()

    def make(name: ProviderName) -> Any:
        def factory(key: str) -> FakeProvider:
            created(name, key)
            return FakeProvider(name, key)

        return factory

    monkeypatch.setattr(
        service_module,
        "_REGISTRY",
        {
            ProviderName.OPENAI: ("openai_api_key", make(ProviderName.OPENAI)),
            ProviderName.ANTHROPIC: (
                "anthropic_api_key",
                make(ProviderName.ANTHROPIC),
            ),
        },
    )
    return created


async def test_two_providers_in_one_instance(factories: MagicMock) -> None:
    service = LLMService(_settings())
    a = await service.generate(
        ModelTargetSchema(provider=ProviderName.OPENAI, model="gpt-x"), "hi"
    )
    b = await service.generate(
        ModelTargetSchema(provider=ProviderName.ANTHROPIC, model="claude-x"),
        "hi",
    )
    assert (a.provider, a.requested_model) == (ProviderName.OPENAI, "gpt-x")
    assert (b.provider, b.requested_model) == (
        ProviderName.ANTHROPIC,
        "claude-x",
    )
    factories.assert_any_call(ProviderName.OPENAI, "sk-openai")
    factories.assert_any_call(ProviderName.ANTHROPIC, "sk-anthropic")


async def test_providers_are_lazy_and_cached(factories: MagicMock) -> None:
    service = LLMService(_settings())
    assert factories.call_count == 0
    target = ModelTargetSchema(provider=ProviderName.OPENAI, model="m")
    await service.generate(target, "a")
    await service.generate(target, "b")
    assert factories.call_count == 1


async def test_request_options_are_forwarded(factories: MagicMock) -> None:
    service = LLMService(_settings())
    target = ModelTargetSchema(provider=ProviderName.OPENAI, model="m")
    await service.generate(
        target,
        "p",
        system_prompt="s",
        temperature=0.2,
        max_tokens=7,
        timeout_seconds=3,
    )
    provider = service._providers[ProviderName.OPENAI]
    request = provider.calls[0]  # type: ignore[attr-defined]
    assert request.model == "m"
    assert request.prompt == "p"
    assert request.system_prompt == "s"
    assert request.temperature == 0.2
    assert request.max_tokens == 7
    assert request.timeout_seconds == 3


async def test_unsupported_provider(factories: MagicMock) -> None:
    target = ModelTargetSchema.model_construct(
        provider="ollama", model="m"  # type: ignore[arg-type]
    )
    with pytest.raises(UnsupportedProviderError, match="ollama"):
        await LLMService(_settings()).generate(target, "p")
    assert factories.call_count == 0


@pytest.mark.parametrize(
    ("provider", "field", "setting"),
    [
        (ProviderName.OPENAI, "openai_api_key", "OPENAI_API_KEY"),
        (ProviderName.ANTHROPIC, "anthropic_api_key", "ANTHROPIC_API_KEY"),
    ],
)
@pytest.mark.parametrize("value", [None, SecretStr(""), SecretStr("   ")])
async def test_missing_or_blank_key(
    factories: MagicMock,
    provider: ProviderName,
    field: str,
    setting: str,
    value: SecretStr | None,
) -> None:
    service = LLMService(_settings(**{field: value}))
    target = ModelTargetSchema(provider=provider, model="m")
    with pytest.raises(ProviderNotConfiguredError, match=setting):
        await service.generate(target, "p")
    assert factories.call_count == 0


async def test_no_shared_state_between_instances(
    factories: MagicMock,
) -> None:
    one = LLMService(_settings(openai_api_key="key-one"))
    two = LLMService(_settings(openai_api_key="key-two"))
    target = ModelTargetSchema(provider=ProviderName.OPENAI, model="m")
    await one.generate(target, "p")
    assert not two._providers
    await two.generate(target, "p")
    assert (
        one._providers[ProviderName.OPENAI]
        is not two._providers[ProviderName.OPENAI]
    )
    assert one._providers[ProviderName.OPENAI].key == "key-one"  # type: ignore[attr-defined]
    assert two._providers[ProviderName.OPENAI].key == "key-two"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prompt": ""},
        {"prompt": "p", "timeout_seconds": 0},
        {"prompt": "p", "max_tokens": 0},
        {"prompt": "p", "temperature": 2.5},
    ],
)
async def test_invalid_request_rejected_before_provider_call(
    factories: MagicMock, kwargs: dict[str, Any]
) -> None:
    service = LLMService(_settings())
    target = ModelTargetSchema(provider=ProviderName.OPENAI, model="m")
    with pytest.raises(ValidationError):
        await service.generate(target, **kwargs)
    provider = service._providers.get(ProviderName.OPENAI)
    assert provider is None or not provider.calls  # type: ignore[attr-defined]


async def test_same_response_shape_across_providers(
    factories: MagicMock,
) -> None:
    service = LLMService(_settings())
    a = await service.generate(
        ModelTargetSchema(provider=ProviderName.OPENAI, model="m"), "hi"
    )
    b = await service.generate(
        ModelTargetSchema(provider=ProviderName.ANTHROPIC, model="m"), "hi"
    )
    assert type(a) is type(b) is LLMResponseSchema
    assert set(a.model_dump()) == set(b.model_dump())

from collections.abc import Callable

from pydantic import SecretStr

from components.core.config import SettingsSchema
from components.third_party.llm.providers.anthropic import AnthropicProvider
from components.third_party.llm.providers.base import BaseLLMProvider
from components.third_party.llm.providers.openai import OpenAIProvider
from components.third_party.llm.schemas import (
    LLMRequestSchema,
    LLMResponseSchema,
    ModelTargetSchema,
    ProviderName,
)


class UnsupportedProviderError(Exception):
    """The target names a provider that has no adapter."""


class ProviderNotConfiguredError(Exception):
    """The provider's API key setting is missing or blank. The message
    names the setting only, never a value."""

    def __init__(self, provider: ProviderName, setting: str) -> None:
        self.provider = provider
        self.setting = setting
        super().__init__(
            f"Provider '{provider.value}' is not configured: " f"set {setting}"
        )


# provider -> (settings attribute holding the key, adapter factory)
_REGISTRY: dict[ProviderName, tuple[str, Callable[[str], BaseLLMProvider]]] = {
    ProviderName.OPENAI: (
        "openai_api_key",
        lambda key: OpenAIProvider(api_key=key),
    ),
    ProviderName.ANTHROPIC: (
        "anthropic_api_key",
        lambda key: AnthropicProvider(api_key=key),
    ),
}


class LLMService:
    """Selects a provider adapter from a ``ModelTargetSchema``. Adapters are
    created lazily and cached on the instance; there is no shared state
    between instances."""

    def __init__(self, settings: SettingsSchema) -> None:
        self._settings = settings
        self._providers: dict[ProviderName, BaseLLMProvider] = {}

    def _get_provider(self, name: ProviderName) -> BaseLLMProvider:
        provider = self._providers.get(name)
        if provider is not None:
            return provider
        entry = _REGISTRY.get(name)
        if entry is None:
            raise UnsupportedProviderError(f"Unsupported provider: {name!s}")
        setting, factory = entry
        secret: SecretStr | None = getattr(self._settings, setting)
        key = secret.get_secret_value().strip() if secret else ""
        if not key:
            raise ProviderNotConfiguredError(name, setting.upper())
        provider = factory(key)
        self._providers[name] = provider
        return provider

    def ensure_provider_ready(self, name: ProviderName) -> None:
        """Raise ``ProviderNotConfiguredError`` (or
        ``UnsupportedProviderError``) unless the provider can be used.
        Makes no network call."""
        self._get_provider(name)

    async def generate(
        self,
        target: ModelTargetSchema,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 60.0,
    ) -> LLMResponseSchema:
        """Run one request against ``target``. The model comes only from the
        target. Raises ``LLMProviderError`` on provider failures."""
        provider = self._get_provider(target.provider)
        request = LLMRequestSchema(
            model=target.model,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        return await provider.generate(request)

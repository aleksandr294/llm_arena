from components.third_party.llm.enums import LLMErrorKind, ProviderName

RETRYABLE_KINDS: frozenset[LLMErrorKind] = frozenset(
    {
        LLMErrorKind.TIMEOUT,
        LLMErrorKind.RATE_LIMIT,
        LLMErrorKind.SERVER_ERROR,
    }
)


class LLMProviderError(Exception):
    """Normalized provider failure. Messages must never contain API keys or
    prompt/response content."""

    def __init__(
        self,
        kind: LLMErrorKind,
        provider: ProviderName,
        message: str = "",
        *,
        retryable: bool | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.kind = kind
        self.provider = provider
        self.retryable = (
            kind in RETRYABLE_KINDS if retryable is None else retryable
        )
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
        text = message or kind.value
        super().__init__(f"{provider.value}: {kind.value}: {text}")


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

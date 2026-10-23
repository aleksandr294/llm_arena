import pytest

from components.third_party.llm.enums import LLMErrorKind, ProviderName
from components.third_party.llm.exceptions import LLMProviderError
from components.third_party.llm.providers.base import BaseLLMProvider

RETRYABLE = {
    LLMErrorKind.TIMEOUT: True,
    LLMErrorKind.RATE_LIMIT: True,
    LLMErrorKind.SERVER_ERROR: True,
    LLMErrorKind.AUTHENTICATION: False,
    LLMErrorKind.INVALID_REQUEST: False,
    LLMErrorKind.MALFORMED_RESPONSE: False,
    LLMErrorKind.UNKNOWN: False,
}


def test_every_kind_has_expected_default() -> None:
    assert set(RETRYABLE) == set(LLMErrorKind)


@pytest.mark.parametrize(("kind", "expected"), list(RETRYABLE.items()))
def test_retryable_defaults(kind: LLMErrorKind, expected: bool) -> None:
    err = LLMProviderError(kind, ProviderName.OPENAI)
    assert err.retryable is expected


def test_retryable_override_and_metadata() -> None:
    err = LLMProviderError(
        LLMErrorKind.RATE_LIMIT,
        ProviderName.ANTHROPIC,
        "slow down",
        retryable=False,
        request_id="req_1",
        retry_after_seconds=2.5,
    )
    assert err.retryable is False
    assert err.request_id == "req_1"
    assert err.retry_after_seconds == 2.5
    assert err.provider is ProviderName.ANTHROPIC
    assert "anthropic" in str(err) and "rate_limit" in str(err)


def test_base_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseLLMProvider()  # type: ignore[abstract]

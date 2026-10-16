from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx2
import pytest

from components.third_party.llm.providers.anthropic import (
    DEFAULT_MAX_TOKENS,
    AnthropicProvider,
)
from components.third_party.llm.providers.base import LLMProviderError
from components.third_party.llm.schemas import (
    LLMErrorKind,
    LLMRequestSchema,
    ProviderName,
)

REQUEST = LLMRequestSchema(model="claude-test", prompt="SECRET-PROMPT")
HTTP_REQUEST = httpx2.Request("POST", "https://example.invalid/v1")


def _text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def _message(
    content: Any = None,
    usage: Any = None,
    request_id: str | None = "req_1",
) -> SimpleNamespace:
    message = SimpleNamespace(
        model="claude-test-2024",
        content=[_text("hello")] if content is None else content,
        stop_reason="end_turn",
        usage=usage,
    )
    if request_id is not None:
        message._request_id = request_id
    return message


def _provider(result: Any = None, error: Exception | None = None) -> Any:
    create = AsyncMock(return_value=result, side_effect=error)
    client = MagicMock()
    client.messages.create = create
    return AnthropicProvider(client=client), create


def _status_error(
    cls: type[anthropic.APIStatusError],
    status: int,
    headers: dict[str, str] | None = None,
) -> anthropic.APIStatusError:
    response = httpx2.Response(
        status, headers=headers or {}, request=HTTP_REQUEST
    )
    return cls("boom SECRET-PROMPT", response=response, body=None)


async def test_success_maps_all_fields() -> None:
    usage = SimpleNamespace(input_tokens=12, output_tokens=30)
    provider, create = _provider(_message(usage=usage))
    result = await provider.generate(REQUEST)
    assert result.text == "hello"
    assert result.provider is ProviderName.ANTHROPIC
    assert result.requested_model == "claude-test"
    assert result.reported_model == "claude-test-2024"
    assert result.finish_reason == "end_turn"
    assert result.provider_request_id == "req_1"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 30
    assert result.usage.total_tokens is None
    assert result.latency_ms >= 0
    kwargs = create.call_args.kwargs
    assert kwargs["timeout"] == 60.0
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS
    assert result.effective_max_tokens == DEFAULT_MAX_TOKENS
    assert "temperature" not in kwargs
    assert "system" not in kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "SECRET-PROMPT"}]


async def test_optional_parameters_are_forwarded() -> None:
    provider, create = _provider(_message())
    result = await provider.generate(
        LLMRequestSchema(
            model="m",
            prompt="p",
            system_prompt="s",
            temperature=0.5,
            max_tokens=10,
            timeout_seconds=5,
        )
    )
    kwargs = create.call_args.kwargs
    assert kwargs["system"] == "s"
    assert kwargs["temperature"] == 0.5
    assert kwargs["max_tokens"] == 10
    assert result.effective_max_tokens == 10
    assert kwargs["timeout"] == 5


async def test_multi_block_content_is_joined_ignoring_non_text() -> None:
    content = [
        _text("one "),
        SimpleNamespace(type="thinking", thinking="hidden"),
        _text("two"),
    ]
    provider, _ = _provider(_message(content=content))
    result = await provider.generate(REQUEST)
    assert result.text == "one two"


async def test_missing_usage_is_null_not_zero() -> None:
    provider, _ = _provider(_message(usage=None))
    result = await provider.generate(REQUEST)
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None
    assert result.usage.total_tokens is None


async def test_missing_request_id_is_null() -> None:
    provider, _ = _provider(_message(request_id=None))
    result = await provider.generate(REQUEST)
    assert result.provider_request_id is None


def test_client_is_built_with_retries_disabled() -> None:
    provider = AnthropicProvider(api_key="sk-test")
    assert provider._client.max_retries == 0


async def test_rate_limit_with_retry_after() -> None:
    error = _status_error(
        anthropic.RateLimitError, 429, {"retry-after": "7", "request-id": "r9"}
    )
    provider, _ = _provider(error=error)
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.RATE_LIMIT
    assert info.value.retryable is True
    assert info.value.retry_after_seconds == 7.0
    assert info.value.request_id == "r9"
    assert "SECRET-PROMPT" not in str(info.value)


async def test_rate_limit_without_retry_after() -> None:
    provider, _ = _provider(error=_status_error(anthropic.RateLimitError, 429))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.retry_after_seconds is None


async def test_authentication_not_retryable() -> None:
    provider, _ = _provider(
        error=_status_error(anthropic.AuthenticationError, 401)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.AUTHENTICATION
    assert info.value.retryable is False


async def test_timeout_retryable() -> None:
    provider, _ = _provider(
        error=anthropic.APITimeoutError(request=HTTP_REQUEST)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.TIMEOUT
    assert info.value.retryable is True


async def test_server_error_retryable() -> None:
    provider, _ = _provider(
        error=_status_error(anthropic.InternalServerError, 529)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.SERVER_ERROR
    assert info.value.retryable is True


async def test_invalid_request_not_retryable() -> None:
    provider, _ = _provider(error=_status_error(anthropic.BadRequestError, 400))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.INVALID_REQUEST
    assert info.value.retryable is False


async def test_connection_error_is_retryable_unknown() -> None:
    provider, _ = _provider(
        error=anthropic.APIConnectionError(request=HTTP_REQUEST)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.UNKNOWN
    assert info.value.retryable is True


async def test_unexpected_exception_is_unknown() -> None:
    provider, _ = _provider(error=RuntimeError("SECRET-PROMPT"))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.UNKNOWN
    assert "SECRET-PROMPT" not in str(info.value)


@pytest.mark.parametrize(
    "content",
    [
        [],
        [_text("")],
        [_text("  ")],
        [SimpleNamespace(type="tool_use")],
    ],
)
async def test_no_text_content_is_malformed(content: Any) -> None:
    provider, _ = _provider(_message(content=content))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.MALFORMED_RESPONSE
    assert info.value.retryable is False


async def test_logs_do_not_contain_prompt_or_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    provider, _ = _provider(_message(content=[_text("SECRET-RESPONSE")]))
    await provider.generate(REQUEST)
    assert "SECRET" not in caplog.text

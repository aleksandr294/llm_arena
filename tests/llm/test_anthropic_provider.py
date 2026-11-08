import asyncio
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx2
import pytest
from anthropic.resources.messages import AsyncMessages

from components.third_party.llm.enums import LLMErrorKind, ProviderName
from components.third_party.llm.exceptions import LLMProviderError
from components.third_party.llm.providers.anthropic import (
    DEFAULT_MAX_TOKENS,
    AnthropicProvider,
)
from components.third_party.llm.schemas import LLMRequestSchema

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
    assert "extra_body" not in kwargs
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
    assert kwargs["extra_body"] == {"temperature": 0.5}
    assert "temperature" not in kwargs
    assert kwargs["max_tokens"] == 10
    assert result.effective_max_tokens == 10
    assert kwargs["timeout"] == 5


async def test_call_keywords_match_the_sdk_signature() -> None:
    # A mocked client accepts any keyword, so check against the real SDK.
    provider, create = _provider(_message())
    await provider.generate(
        LLMRequestSchema(
            model="m",
            prompt="p",
            system_prompt="s",
            temperature=0.5,
            max_tokens=10,
            timeout_seconds=5,
        )
    )
    accepted = inspect.signature(AsyncMessages.create).parameters
    assert set(create.call_args.kwargs) <= set(accepted)


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


async def test_connection_error_is_retryable_connection() -> None:
    provider, _ = _provider(
        error=anthropic.APIConnectionError(request=HTTP_REQUEST)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.CONNECTION
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


# --- cache tokens, empty completions, injected client ----------------------


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (
            SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=20,
                cache_read_input_tokens=90,
            ),
            120,
        ),
        (
            SimpleNamespace(
                input_tokens=10, output_tokens=5, cache_read_input_tokens=90
            ),
            100,
        ),
        (SimpleNamespace(input_tokens=10, output_tokens=5), 10),
        (
            SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=None,
                cache_read_input_tokens=None,
            ),
            10,
        ),
    ],
)
async def test_input_tokens_include_cache_tokens(
    usage: Any, expected: int
) -> None:
    provider, _ = _provider(_message(usage=usage))
    result = await provider.generate(REQUEST)
    assert result.usage.input_tokens == expected
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens is None


async def test_usage_stays_unknown_when_not_reported() -> None:
    provider, _ = _provider(_message(usage=None))
    result = await provider.generate(REQUEST)
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None


async def test_empty_completion_keeps_finish_reason_and_usage() -> None:
    usage = SimpleNamespace(input_tokens=7, output_tokens=4096)
    message = _message(content=[], usage=usage)
    message.stop_reason = "max_tokens"
    provider, _ = _provider(message)
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    err = info.value
    assert err.kind is LLMErrorKind.MALFORMED_RESPONSE
    assert err.finish_reason == "max_tokens"
    assert (err.input_tokens, err.output_tokens) == (7, 4096)
    assert err.total_tokens is None


async def test_empty_completion_without_usage_is_unknown() -> None:
    provider, _ = _provider(_message(content=[], usage=None))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.input_tokens is None
    assert info.value.output_tokens is None


def test_falsy_injected_client_is_used() -> None:
    client = MagicMock()
    client.__bool__.return_value = False
    assert AnthropicProvider(client=client)._client is client


# --- streaming (LLM_MEASURE_TTFT) -------------------------------------------


def test_installed_sdk_exposes_the_streaming_api() -> None:
    from anthropic.lib.streaming import AsyncMessageStream

    assert "stream" in dir(AsyncMessages)
    assert hasattr(AsyncMessageStream, "get_final_message")
    assert "text_stream" in AsyncMessageStream.__annotations__
    assert isinstance(AsyncMessageStream.request_id, property)


class FakeStream:
    def __init__(
        self,
        chunks: list[str],
        message: Any,
        fail_after: int | None = None,
        delay: float = 0.0,
        request_id: str | None = "req_stream",
    ) -> None:
        self._chunks = chunks
        self._message = message
        self._fail_after = fail_after
        self._delay = delay
        self.request_id = request_id
        self.text_stream = self._text()

    async def _text(self) -> Any:
        for index, chunk in enumerate(self._chunks):
            if self._fail_after is not None and index == self._fail_after:
                raise anthropic.APIConnectionError(request=HTTP_REQUEST)
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk

    async def get_final_message(self) -> Any:
        return self._message

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _streaming_provider(stream: FakeStream) -> Any:
    client = MagicMock()
    client.messages.stream = MagicMock(return_value=stream)
    client.messages.create = AsyncMock(side_effect=AssertionError("no create"))
    return AnthropicProvider(client=client, measure_ttft=True), client


async def test_streaming_sets_ttft_and_matches_non_streaming_result() -> None:
    usage = SimpleNamespace(input_tokens=12, output_tokens=30)
    message = _message(usage=usage, request_id=None)
    plain_provider, _ = _provider(
        _message(usage=usage, request_id="req_stream")
    )
    plain = await plain_provider.generate(REQUEST)

    provider, client = _streaming_provider(
        FakeStream(["", "hel", "lo"], message)
    )
    result = await provider.generate(REQUEST)

    assert plain.time_to_first_token_ms is None
    assert result.time_to_first_token_ms is not None
    assert 0 <= result.time_to_first_token_ms <= result.latency_ms
    for field in (
        "text",
        "reported_model",
        "finish_reason",
        "provider_request_id",
        "usage",
        "effective_max_tokens",
    ):
        assert getattr(result, field) == getattr(plain, field)
    kwargs = client.messages.stream.call_args.kwargs
    assert kwargs["timeout"] == 60.0 and kwargs["max_tokens"]


async def test_streaming_forwards_temperature_and_system() -> None:
    provider, client = _streaming_provider(FakeStream(["a"], _message()))
    await provider.generate(
        LLMRequestSchema(
            model="m", prompt="p", system_prompt="s", temperature=0.3
        )
    )
    kwargs = client.messages.stream.call_args.kwargs
    assert kwargs["system"] == "s"
    assert kwargs["extra_body"] == {"temperature": 0.3}


async def test_empty_leading_chunks_do_not_stamp_ttft() -> None:
    provider, _ = _streaming_provider(
        FakeStream(["", "", "x"], _message(), delay=0.02)
    )
    result = await provider.generate(REQUEST)
    # Only the delay before the first non-empty chunk counts: >= 3 sleeps.
    assert result.time_to_first_token_ms is not None
    assert result.time_to_first_token_ms >= 50


async def test_streaming_without_text_chunks_leaves_ttft_unknown() -> None:
    provider, _ = _streaming_provider(FakeStream([], _message()))
    result = await provider.generate(REQUEST)
    assert result.text == "hello"
    assert result.time_to_first_token_ms is None


async def test_stream_failure_midway_is_normalized() -> None:
    provider, _ = _streaming_provider(
        FakeStream(["a", "b"], _message(), fail_after=1)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.CONNECTION
    assert info.value.retryable is True


async def test_stream_overall_timeout_maps_to_timeout() -> None:
    provider, _ = _streaming_provider(
        FakeStream(["a", "b"], _message(), delay=0.5)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(
            LLMRequestSchema(model="m", prompt="p", timeout_seconds=0.05)
        )
    assert info.value.kind is LLMErrorKind.TIMEOUT
    assert info.value.retryable is True


async def test_streaming_empty_completion_keeps_usage() -> None:
    usage = SimpleNamespace(input_tokens=3, output_tokens=9)
    message = _message(content=[], usage=usage)
    provider, _ = _streaming_provider(FakeStream([], message))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.MALFORMED_RESPONSE
    assert info.value.output_tokens == 9

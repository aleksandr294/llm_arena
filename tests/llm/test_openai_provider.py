import asyncio
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
import openai
import pytest

from components.third_party.llm.enums import LLMErrorKind, ProviderName
from components.third_party.llm.exceptions import LLMProviderError
from components.third_party.llm.providers.openai import OpenAIProvider
from components.third_party.llm.schemas import LLMRequestSchema

REQUEST = LLMRequestSchema(model="gpt-test", prompt="SECRET-PROMPT")
HTTP_REQUEST = httpx2.Request("POST", "https://example.invalid/v1")


def _completion(
    content: Any = "hello",
    usage: Any = None,
    request_id: str | None = "req_1",
    choices: list[Any] | None = None,
) -> SimpleNamespace:
    if choices is None:
        choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ]
    completion = SimpleNamespace(
        model="gpt-test-2024", choices=choices, usage=usage
    )
    if request_id is not None:
        completion._request_id = request_id
    return completion


def _provider(result: Any = None, error: Exception | None = None) -> Any:
    create = AsyncMock(return_value=result, side_effect=error)
    client = MagicMock()
    client.chat.completions.create = create
    return OpenAIProvider(client=client), create


def _status_error(
    cls: type[openai.APIStatusError],
    status: int,
    headers: dict[str, str] | None = None,
) -> openai.APIStatusError:
    response = httpx2.Response(
        status, headers=headers or {}, request=HTTP_REQUEST
    )
    return cls("boom SECRET-PROMPT", response=response, body=None)


async def test_success_maps_all_fields() -> None:
    usage = SimpleNamespace(
        prompt_tokens=12, completion_tokens=30, total_tokens=42
    )
    provider, create = _provider(_completion(usage=usage))
    result = await provider.generate(REQUEST)
    assert result.text == "hello"
    assert result.provider is ProviderName.OPENAI
    assert result.requested_model == "gpt-test"
    assert result.reported_model == "gpt-test-2024"
    assert result.finish_reason == "stop"
    assert result.provider_request_id == "req_1"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 30
    assert result.usage.total_tokens == 42
    assert result.latency_ms >= 0
    kwargs = create.call_args.kwargs
    assert kwargs["timeout"] == 60.0
    assert "temperature" not in kwargs
    assert "max_completion_tokens" not in kwargs
    assert result.effective_max_tokens is None
    assert kwargs["messages"] == [{"role": "user", "content": "SECRET-PROMPT"}]


async def test_optional_parameters_are_forwarded() -> None:
    provider, create = _provider(_completion())
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
    assert kwargs["temperature"] == 0.5
    assert kwargs["max_completion_tokens"] == 10
    assert result.effective_max_tokens == 10
    assert kwargs["timeout"] == 5
    assert kwargs["messages"][0] == {"role": "system", "content": "s"}


async def test_missing_usage_is_null_not_zero() -> None:
    provider, _ = _provider(_completion(usage=None))
    result = await provider.generate(REQUEST)
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None
    assert result.usage.total_tokens is None


async def test_missing_request_id_is_null() -> None:
    provider, _ = _provider(_completion(request_id=None))
    result = await provider.generate(REQUEST)
    assert result.provider_request_id is None


def test_client_is_built_with_retries_disabled() -> None:
    provider = OpenAIProvider(api_key="sk-test")
    assert provider._client.max_retries == 0


async def test_rate_limit_with_retry_after() -> None:
    error = _status_error(
        openai.RateLimitError, 429, {"retry-after": "7", "x-request-id": "r9"}
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
    provider, _ = _provider(error=_status_error(openai.RateLimitError, 429))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.retry_after_seconds is None


async def test_authentication_not_retryable() -> None:
    provider, _ = _provider(
        error=_status_error(openai.AuthenticationError, 401)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.AUTHENTICATION
    assert info.value.retryable is False


async def test_timeout_retryable() -> None:
    provider, _ = _provider(error=openai.APITimeoutError(request=HTTP_REQUEST))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.TIMEOUT
    assert info.value.retryable is True


async def test_server_error_retryable() -> None:
    provider, _ = _provider(
        error=_status_error(openai.InternalServerError, 500)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.SERVER_ERROR
    assert info.value.retryable is True


async def test_invalid_request_not_retryable() -> None:
    provider, _ = _provider(error=_status_error(openai.BadRequestError, 400))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.INVALID_REQUEST
    assert info.value.retryable is False


async def test_connection_error_is_retryable_connection() -> None:
    provider, _ = _provider(
        error=openai.APIConnectionError(request=HTTP_REQUEST)
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
    assert info.value.retryable is False
    assert "SECRET-PROMPT" not in str(info.value)


@pytest.mark.parametrize("content", [None, "", "   "])
async def test_empty_content_is_malformed(content: Any) -> None:
    provider, _ = _provider(_completion(content=content))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.MALFORMED_RESPONSE
    assert info.value.retryable is False


async def test_no_choices_is_malformed() -> None:
    provider, _ = _provider(_completion(choices=[]))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.MALFORMED_RESPONSE


async def test_logs_do_not_contain_prompt_or_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    provider, _ = _provider(_completion(content="SECRET-RESPONSE"))
    await provider.generate(REQUEST)
    assert "SECRET" not in caplog.text


# --- empty completions, injected client -------------------------------------


async def test_empty_content_with_length_keeps_finish_reason_and_usage() -> (
    None
):
    usage = SimpleNamespace(
        prompt_tokens=5, completion_tokens=256, total_tokens=261
    )
    completion = _completion(content="", usage=usage)
    completion.choices[0].finish_reason = "length"
    provider, _ = _provider(completion)
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    err = info.value
    assert err.kind is LLMErrorKind.MALFORMED_RESPONSE
    assert err.retryable is False
    assert err.finish_reason == "length"
    assert (err.input_tokens, err.output_tokens, err.total_tokens) == (
        5,
        256,
        261,
    )
    assert "SECRET-PROMPT" not in str(err)


async def test_empty_content_without_usage_is_unknown() -> None:
    provider, _ = _provider(_completion(content=None, usage=None))
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.finish_reason == "stop"
    assert info.value.input_tokens is None
    assert info.value.output_tokens is None


def test_falsy_injected_client_is_used() -> None:
    client = MagicMock()
    client.__bool__.return_value = False
    assert OpenAIProvider(client=client)._client is client


# --- streaming (LLM_MEASURE_TTFT) -------------------------------------------


def test_installed_sdk_exposes_the_streaming_api() -> None:
    from openai.resources.chat.completions import AsyncCompletions

    parameters = inspect.signature(AsyncCompletions.create).parameters
    assert "stream" in parameters and "stream_options" in parameters
    assert hasattr(openai.AsyncStream, "close")


def _chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
    choices: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        model="gpt-test-2024",
        choices=(
            [
                SimpleNamespace(
                    delta=SimpleNamespace(content=content),
                    finish_reason=finish_reason,
                )
            ]
            if choices
            else []
        ),
        usage=usage,
    )


class FakeStream:
    def __init__(
        self,
        chunks: list[Any],
        fail_after: int | None = None,
        delay: float = 0.0,
    ) -> None:
        self._chunks = chunks
        self._fail_after = fail_after
        self._delay = delay
        self.response = SimpleNamespace(headers={"x-request-id": "req_s"})

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for index, chunk in enumerate(self._chunks):
            if self._fail_after is not None and index == self._fail_after:
                raise openai.APIConnectionError(request=HTTP_REQUEST)
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


USAGE = SimpleNamespace(prompt_tokens=12, completion_tokens=30, total_tokens=42)


def _streaming_provider(stream: FakeStream) -> Any:
    create = AsyncMock(return_value=stream)
    client = MagicMock()
    client.chat.completions.create = create
    return OpenAIProvider(client=client, measure_ttft=True), create


async def test_streaming_sets_ttft_and_matches_non_streaming_result() -> None:
    plain_provider, _ = _provider(_completion(usage=USAGE, request_id="req_s"))
    plain = await plain_provider.generate(REQUEST)

    provider, create = _streaming_provider(
        FakeStream(
            [
                _chunk(content=None),
                _chunk(content=""),
                _chunk(content="hel"),
                _chunk(content="lo"),
                _chunk(finish_reason="stop"),
                _chunk(usage=USAGE, choices=False),
            ]
        )
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
    kwargs = create.call_args.kwargs
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["timeout"] == 60.0


async def test_empty_leading_deltas_do_not_stamp_ttft() -> None:
    provider, _ = _streaming_provider(
        FakeStream(
            [_chunk(content=None), _chunk(content=""), _chunk(content="x")],
            delay=0.02,
        )
    )
    result = await provider.generate(REQUEST)
    assert result.time_to_first_token_ms is not None
    assert result.time_to_first_token_ms >= 50


async def test_streaming_usage_is_none_when_no_usage_chunk() -> None:
    provider, _ = _streaming_provider(
        FakeStream([_chunk(content="hi"), _chunk(finish_reason="stop")])
    )
    result = await provider.generate(REQUEST)
    assert result.text == "hi"
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None
    assert result.usage.total_tokens is None


async def test_stream_failure_midway_is_normalized() -> None:
    provider, _ = _streaming_provider(
        FakeStream([_chunk(content="a"), _chunk(content="b")], fail_after=1)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    assert info.value.kind is LLMErrorKind.CONNECTION
    assert info.value.retryable is True


async def test_stream_overall_timeout_maps_to_timeout() -> None:
    provider, _ = _streaming_provider(
        FakeStream([_chunk(content="a"), _chunk(content="b")], delay=0.5)
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(
            LLMRequestSchema(model="m", prompt="p", timeout_seconds=0.05)
        )
    assert info.value.kind is LLMErrorKind.TIMEOUT


async def test_streaming_empty_completion_keeps_length_and_usage() -> None:
    provider, _ = _streaming_provider(
        FakeStream(
            [_chunk(finish_reason="length"), _chunk(usage=USAGE, choices=False)]
        )
    )
    with pytest.raises(LLMProviderError) as info:
        await provider.generate(REQUEST)
    err = info.value
    assert err.kind is LLMErrorKind.MALFORMED_RESPONSE
    assert err.finish_reason == "length"
    assert err.output_tokens == 30

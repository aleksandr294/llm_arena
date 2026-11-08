import asyncio
import time
from typing import Any

import openai
from openai import AsyncOpenAI

from components.third_party.llm.enums import LLMErrorKind, ProviderName
from components.third_party.llm.exceptions import LLMProviderError
from components.third_party.llm.providers.base import BaseLLMProvider
from components.third_party.llm.schemas import (
    LLMRequestSchema,
    LLMResponseSchema,
    LLMUsageSchema,
)
from components.third_party.llm.utils import SDKErrors, log_call, map_sdk_error

_SDK_ERRORS = SDKErrors(
    timeout=openai.APITimeoutError,
    connection=openai.APIConnectionError,
    status=openai.APIStatusError,
    validation=openai.APIResponseValidationError,
)


class _Completion:
    """What the adapter needs from a (streamed or whole) completion."""

    def __init__(
        self,
        text: Any,
        finish_reason: str | None,
        model: str | None,
        raw_usage: Any,
        request_id: str | None,
        ttft_ms: float | None = None,
    ) -> None:
        self.text = text
        self.finish_reason = finish_reason
        self.model = model
        self.raw_usage = raw_usage
        self.request_id = request_id
        self.ttft_ms = ttft_ms


class OpenAIProvider(BaseLLMProvider):
    name = ProviderName.OPENAI

    def __init__(
        self,
        api_key: str | None = None,
        client: AsyncOpenAI | None = None,
        measure_ttft: bool = False,
    ) -> None:
        # SDK retries are disabled: the platform owns the retry policy.
        self._client = (
            AsyncOpenAI(api_key=api_key, max_retries=0)
            if client is None
            else client
        )
        self._measure_ttft = measure_ttft

    async def generate(self, request: LLMRequestSchema) -> LLMResponseSchema:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append(
                {"role": "system", "content": request.system_prompt}
            )
        messages.append({"role": "user", "content": request.prompt})

        params: dict[str, Any] = {}
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.max_tokens is not None:
            params["max_completion_tokens"] = request.max_tokens
        call: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "timeout": request.timeout_seconds,
            **params,
        }

        started = time.perf_counter()
        try:
            if self._measure_ttft:
                result = await self._stream(request, call, started)
            else:
                result = await self._create(call)
        except Exception as exc:
            log_call(self.name, request, "error", started)
            raise self._error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        usage = LLMUsageSchema()
        raw_usage = result.raw_usage
        if raw_usage is not None:
            usage = LLMUsageSchema(
                input_tokens=raw_usage.prompt_tokens,
                output_tokens=raw_usage.completion_tokens,
                total_tokens=raw_usage.total_tokens,
            )
        text = result.text
        if not isinstance(text, str) or not text.strip():
            log_call(self.name, request, "malformed_response", started)
            raise LLMProviderError(
                LLMErrorKind.MALFORMED_RESPONSE,
                self.name,
                "response contained no text content",
                request_id=result.request_id,
                finish_reason=result.finish_reason,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
            )

        log_call(self.name, request, "success", started)
        return LLMResponseSchema(
            text=text,
            provider=self.name,
            requested_model=request.model,
            reported_model=result.model,
            finish_reason=result.finish_reason,
            provider_request_id=result.request_id,
            usage=usage,
            effective_max_tokens=request.max_tokens,
            latency_ms=latency_ms,
            time_to_first_token_ms=result.ttft_ms,
        )

    async def _create(self, call: dict[str, Any]) -> _Completion:
        completion = await self._client.chat.completions.create(**call)
        try:
            choice = completion.choices[0]
            text = choice.message.content
            finish_reason = choice.finish_reason
        except (AttributeError, IndexError, TypeError):
            text, finish_reason = None, None
        return _Completion(
            text,
            finish_reason,
            getattr(completion, "model", None),
            getattr(completion, "usage", None),
            getattr(completion, "_request_id", None),
        )

    async def _stream(
        self, request: LLMRequestSchema, call: dict[str, Any], started: float
    ) -> _Completion:
        """Consume the stream and stamp the first non-empty content delta.
        The SDK timeout is per read for streams, so the whole call is
        bounded here. Partial text is discarded on any error. Usage is only
        reported on the final chunk with ``include_usage``; without it the
        usage stays unknown."""
        parts: list[str] = []
        finish_reason: str | None = None
        model: str | None = None
        raw_usage: Any = None
        ttft_ms: float | None = None
        async with asyncio.timeout(request.timeout_seconds):
            stream = await self._client.chat.completions.create(
                stream=True,
                stream_options={"include_usage": True},
                **call,
            )
            async with stream:
                async for chunk in stream:
                    model = getattr(chunk, "model", None) or model
                    if getattr(chunk, "usage", None) is not None:
                        raw_usage = chunk.usage
                    for choice in getattr(chunk, "choices", None) or []:
                        content = getattr(choice.delta, "content", None)
                        if isinstance(content, str) and content:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - started) * 1000
                            parts.append(content)
                        if choice.finish_reason:
                            finish_reason = choice.finish_reason
        headers = getattr(getattr(stream, "response", None), "headers", None)
        request_id = headers.get("x-request-id") if headers else None
        return _Completion(
            "".join(parts),
            finish_reason,
            model,
            raw_usage,
            request_id,
            ttft_ms,
        )

    def _error(self, exc: Exception) -> LLMProviderError:
        if isinstance(exc, TimeoutError):  # the overall streaming bound
            return LLMProviderError(
                LLMErrorKind.TIMEOUT, self.name, "request timed out"
            )
        return map_sdk_error(self.name, exc, _SDK_ERRORS)

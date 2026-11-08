import asyncio
import time
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

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
    timeout=anthropic.APITimeoutError,
    connection=anthropic.APIConnectionError,
    status=anthropic.APIStatusError,
    validation=anthropic.APIResponseValidationError,
)

# The Messages API requires max_tokens, so a value must be sent when the
# caller omitted it. This is the documented exception to "no invented
# defaults" (see the llm-provider-adapters spec); the value actually used is
# reported as effective_max_tokens on the response.
DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider(BaseLLMProvider):
    name = ProviderName.ANTHROPIC

    def __init__(
        self,
        api_key: str | None = None,
        client: AsyncAnthropic | None = None,
        measure_ttft: bool = False,
    ) -> None:
        # SDK retries are disabled: the platform owns the retry policy.
        self._client = (
            AsyncAnthropic(api_key=api_key, max_retries=0)
            if client is None
            else client
        )
        self._measure_ttft = measure_ttft

    async def generate(self, request: LLMRequestSchema) -> LLMResponseSchema:
        params: dict[str, Any] = {}
        if request.system_prompt:
            params["system"] = request.system_prompt
        if request.temperature is not None:
            # The installed SDK's messages.create has no temperature
            # parameter; extra_body puts it into the JSON request body.
            params["extra_body"] = {"temperature": request.temperature}

        max_tokens = request.max_tokens or DEFAULT_MAX_TOKENS
        call: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": max_tokens,
            "timeout": request.timeout_seconds,
            **params,
        }
        started = time.perf_counter()
        ttft_ms: float | None = None
        request_id: str | None = None
        try:
            if self._measure_ttft:
                message, ttft_ms, request_id = await self._stream(
                    request, call, started
                )
            else:
                message = await self._client.messages.create(**call)
                request_id = getattr(message, "_request_id", None)
        except Exception as exc:
            log_call(self.name, request, "error", started)
            raise self._error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        blocks = getattr(message, "content", None)
        parts: list[str] = []
        if isinstance(blocks, list):
            for block in blocks:
                if getattr(block, "type", None) == "text":
                    block_text = getattr(block, "text", None)
                    if isinstance(block_text, str):
                        parts.append(block_text)
        text = "".join(parts)
        usage = self._usage(getattr(message, "usage", None))
        if not text.strip():
            log_call(self.name, request, "malformed_response", started)
            raise LLMProviderError(
                LLMErrorKind.MALFORMED_RESPONSE,
                self.name,
                "response contained no text content",
                request_id=request_id,
                finish_reason=getattr(message, "stop_reason", None),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
            )

        log_call(self.name, request, "success", started)
        return LLMResponseSchema(
            text=text,
            provider=self.name,
            requested_model=request.model,
            reported_model=message.model,
            finish_reason=message.stop_reason,
            provider_request_id=request_id,
            usage=usage,
            effective_max_tokens=max_tokens,
            latency_ms=latency_ms,
            time_to_first_token_ms=ttft_ms,
        )

    async def _stream(
        self, request: LLMRequestSchema, call: dict[str, Any], started: float
    ) -> tuple[Any, float | None, str | None]:
        """Consume the stream and stamp the first non-empty text chunk. The
        SDK timeout is per read for streams, so the whole call is bounded
        here. Partial text is discarded; the final message is the result."""
        ttft_ms: float | None = None
        async with asyncio.timeout(request.timeout_seconds):
            async with self._client.messages.stream(**call) as stream:
                async for chunk in stream.text_stream:
                    if chunk and ttft_ms is None:
                        ttft_ms = (time.perf_counter() - started) * 1000
                message = await stream.get_final_message()
                # The final message does not carry the request id.
                request_id = stream.request_id
        return message, ttft_ms, request_id

    def _error(self, exc: Exception) -> LLMProviderError:
        if isinstance(exc, TimeoutError):  # the overall streaming bound
            return LLMProviderError(
                LLMErrorKind.TIMEOUT, self.name, "request timed out"
            )
        return map_sdk_error(self.name, exc, _SDK_ERRORS)

    @staticmethod
    def _usage(raw: Any) -> LLMUsageSchema:
        """Anthropic reports no total; it stays null rather than being
        summed. Input tokens include cache creation and cache read tokens
        when reported; absent or ``None`` fields count as nothing."""
        if raw is None:
            return LLMUsageSchema()
        input_tokens = getattr(raw, "input_tokens", None)
        if input_tokens is not None:
            for field in (
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ):
                extra = getattr(raw, field, None)
                if isinstance(extra, int):
                    input_tokens += extra
        return LLMUsageSchema(
            input_tokens=input_tokens,
            output_tokens=getattr(raw, "output_tokens", None),
        )

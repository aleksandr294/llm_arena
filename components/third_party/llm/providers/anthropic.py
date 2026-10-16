import logging
import time
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from components.third_party.llm.providers.base import (
    BaseLLMProvider,
    LLMProviderError,
)
from components.third_party.llm.schemas import (
    LLMErrorKind,
    LLMRequestSchema,
    LLMResponseSchema,
    LLMUsageSchema,
    ProviderName,
)

logger = logging.getLogger(__name__)

# The Messages API requires max_tokens, so a value must be sent when the
# caller omitted it. This is the documented exception to "no invented
# defaults" (see the llm-provider-adapters spec); the value actually used is
# reported as effective_max_tokens on the response.
DEFAULT_MAX_TOKENS = 4096


def _retry_after(error: anthropic.APIStatusError) -> float | None:
    raw = error.response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _status_kind(status: int) -> LLMErrorKind:
    if status == 429:
        return LLMErrorKind.RATE_LIMIT
    if status in (401, 403):
        return LLMErrorKind.AUTHENTICATION
    if status in (400, 404, 413, 422):
        return LLMErrorKind.INVALID_REQUEST
    if status >= 500:  # includes 529 overloaded
        return LLMErrorKind.SERVER_ERROR
    return LLMErrorKind.UNKNOWN


class AnthropicProvider(BaseLLMProvider):
    name = ProviderName.ANTHROPIC

    def __init__(
        self,
        api_key: str | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        # SDK retries are disabled: the platform owns the retry policy.
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=0)

    async def generate(self, request: LLMRequestSchema) -> LLMResponseSchema:
        params: dict[str, Any] = {}
        if request.system_prompt:
            params["system"] = request.system_prompt
        if request.temperature is not None:
            params["temperature"] = request.temperature

        max_tokens = request.max_tokens or DEFAULT_MAX_TOKENS
        started = time.perf_counter()
        try:
            message = await self._client.messages.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                max_tokens=max_tokens,
                timeout=request.timeout_seconds,
                **params,
            )
        except Exception as exc:
            self._log(request, "error", started)
            raise self._map_error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        request_id = getattr(message, "_request_id", None)
        blocks = getattr(message, "content", None)
        parts: list[str] = []
        if isinstance(blocks, list):
            for block in blocks:
                if getattr(block, "type", None) == "text":
                    block_text = getattr(block, "text", None)
                    if isinstance(block_text, str):
                        parts.append(block_text)
        text = "".join(parts)
        if not text.strip():
            self._log(request, "malformed_response", started)
            raise LLMProviderError(
                LLMErrorKind.MALFORMED_RESPONSE,
                self.name,
                "response contained no text content",
                request_id=request_id,
            )

        # Anthropic reports no total; it stays null rather than being summed.
        usage = LLMUsageSchema()
        raw_usage = getattr(message, "usage", None)
        if raw_usage is not None:
            usage = LLMUsageSchema(
                input_tokens=raw_usage.input_tokens,
                output_tokens=raw_usage.output_tokens,
            )
        self._log(request, "success", started)
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
        )

    def _log(
        self, request: LLMRequestSchema, outcome: str, started: float
    ) -> None:
        # Never log keys, prompts or responses.
        logger.info(
            "llm call provider=%s model=%s outcome=%s latency_ms=%.1f",
            self.name.value,
            request.model,
            outcome,
            (time.perf_counter() - started) * 1000,
        )

    def _map_error(self, exc: Exception) -> LLMProviderError:
        # Messages deliberately omit str(exc): SDK text may echo request data.
        if isinstance(exc, anthropic.APITimeoutError):
            return LLMProviderError(
                LLMErrorKind.TIMEOUT, self.name, "request timed out"
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMProviderError(
                LLMErrorKind.UNKNOWN,
                self.name,
                "connection error",
                retryable=True,
            )
        if isinstance(exc, anthropic.APIStatusError):
            kind = _status_kind(exc.status_code)
            return LLMProviderError(
                kind,
                self.name,
                f"http status {exc.status_code}",
                request_id=exc.request_id,
                retry_after_seconds=_retry_after(exc),
            )
        if isinstance(exc, anthropic.APIResponseValidationError):
            return LLMProviderError(
                LLMErrorKind.MALFORMED_RESPONSE,
                self.name,
                "response failed validation",
            )
        return LLMProviderError(
            LLMErrorKind.UNKNOWN, self.name, type(exc).__name__
        )

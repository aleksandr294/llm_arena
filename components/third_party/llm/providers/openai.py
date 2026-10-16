import logging
import time
from typing import Any

import openai
from openai import AsyncOpenAI

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


def _retry_after(error: openai.APIStatusError) -> float | None:
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
    if status >= 500:
        return LLMErrorKind.SERVER_ERROR
    return LLMErrorKind.UNKNOWN


class OpenAIProvider(BaseLLMProvider):
    name = ProviderName.OPENAI

    def __init__(
        self,
        api_key: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        # SDK retries are disabled: the platform owns the retry policy.
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=0)

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

        started = time.perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                model=request.model,
                messages=messages,  # type: ignore[arg-type]
                timeout=request.timeout_seconds,
                **params,
            )
        except Exception as exc:
            self._log(request, "error", started)
            raise self._map_error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        request_id = getattr(completion, "_request_id", None)
        try:
            choice = completion.choices[0]
            text = choice.message.content
            finish_reason = choice.finish_reason
        except (AttributeError, IndexError, TypeError):
            text, finish_reason = None, None
        if not isinstance(text, str) or not text.strip():
            self._log(request, "malformed_response", started)
            raise LLMProviderError(
                LLMErrorKind.MALFORMED_RESPONSE,
                self.name,
                "response contained no text content",
                request_id=request_id,
            )

        usage = LLMUsageSchema()
        raw_usage = getattr(completion, "usage", None)
        if raw_usage is not None:
            usage = LLMUsageSchema(
                input_tokens=raw_usage.prompt_tokens,
                output_tokens=raw_usage.completion_tokens,
                total_tokens=raw_usage.total_tokens,
            )
        self._log(request, "success", started)
        return LLMResponseSchema(
            text=text,
            provider=self.name,
            requested_model=request.model,
            reported_model=completion.model,
            finish_reason=finish_reason,
            provider_request_id=request_id,
            usage=usage,
            effective_max_tokens=request.max_tokens,
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
        if isinstance(exc, openai.APITimeoutError):
            return LLMProviderError(
                LLMErrorKind.TIMEOUT, self.name, "request timed out"
            )
        if isinstance(exc, openai.APIConnectionError):
            return LLMProviderError(
                LLMErrorKind.UNKNOWN,
                self.name,
                "connection error",
                retryable=True,
            )
        if isinstance(exc, openai.APIStatusError):
            kind = _status_kind(exc.status_code)
            return LLMProviderError(
                kind,
                self.name,
                f"http status {exc.status_code}",
                request_id=exc.request_id,
                retry_after_seconds=_retry_after(exc),
            )
        if isinstance(exc, openai.APIResponseValidationError):
            return LLMProviderError(
                LLMErrorKind.MALFORMED_RESPONSE,
                self.name,
                "response failed validation",
            )
        return LLMProviderError(
            LLMErrorKind.UNKNOWN, self.name, type(exc).__name__
        )

import logging
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass

from components.third_party.llm.enums import LLMErrorKind, ProviderName
from components.third_party.llm.exceptions import LLMProviderError
from components.third_party.llm.schemas import LLMRequestSchema

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SDKErrors:
    """The four SDK exception classes an adapter hands to
    ``map_sdk_error``; SDK knowledge stays in the adapter."""

    timeout: type[Exception]
    connection: type[Exception]
    status: type[Exception]
    validation: type[Exception]


def retry_after(headers: Mapping[str, str]) -> float | None:
    """Seconds from a ``retry-after`` response header, or None when absent,
    non-numeric, non-finite (``inf``, ``nan``) or negative."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def status_kind(status: int) -> LLMErrorKind:
    if status == 429:
        return LLMErrorKind.RATE_LIMIT
    if status in (401, 403):
        return LLMErrorKind.AUTHENTICATION
    if status in (400, 404, 413, 422):
        return LLMErrorKind.INVALID_REQUEST
    if status >= 500:  # includes Anthropic's 529 overloaded
        return LLMErrorKind.SERVER_ERROR
    return LLMErrorKind.UNKNOWN


def log_call(
    provider: ProviderName,
    request: LLMRequestSchema,
    outcome: str,
    started: float,
) -> None:
    # Never log keys, prompts or responses.
    logger.info(
        "llm call provider=%s model=%s outcome=%s latency_ms=%.1f",
        provider.value,
        request.model,
        outcome,
        (time.perf_counter() - started) * 1000,
    )


def map_sdk_error(
    provider: ProviderName, exc: Exception, errors: SDKErrors
) -> LLMProviderError:
    # Messages deliberately omit str(exc): SDK text may echo request data.
    # The timeout class subclasses the connection class in both SDKs, so it
    # is checked first.
    if isinstance(exc, errors.timeout):
        return LLMProviderError(
            LLMErrorKind.TIMEOUT, provider, "request timed out"
        )
    if isinstance(exc, errors.connection):
        return LLMProviderError(
            LLMErrorKind.CONNECTION, provider, "connection error"
        )
    if isinstance(exc, errors.status):
        status_code: int = exc.status_code  # type: ignore[attr-defined]
        return LLMProviderError(
            status_kind(status_code),
            provider,
            f"http status {status_code}",
            request_id=exc.request_id,  # type: ignore[attr-defined]
            retry_after_seconds=retry_after(
                exc.response.headers  # type: ignore[attr-defined]
            ),
        )
    if isinstance(exc, errors.validation):
        return LLMProviderError(
            LLMErrorKind.MALFORMED_RESPONSE,
            provider,
            "response failed validation",
        )
    return LLMProviderError(LLMErrorKind.UNKNOWN, provider, type(exc).__name__)

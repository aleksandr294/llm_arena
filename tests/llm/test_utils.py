import httpx2
import openai
import pytest

from components.third_party.llm.enums import LLMErrorKind, ProviderName
from components.third_party.llm.schemas import LLMRequestSchema
from components.third_party.llm.utils import (
    SDKErrors,
    log_call,
    map_sdk_error,
    retry_after,
)

ERRORS = SDKErrors(
    timeout=openai.APITimeoutError,
    connection=openai.APIConnectionError,
    status=openai.APIStatusError,
    validation=openai.APIResponseValidationError,
)
HTTP_REQUEST = httpx2.Request("POST", "https://example.invalid/v1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7", 7.0),
        ("0", 0.0),
        ("1.5", 1.5),
        ("inf", None),
        ("Infinity", None),
        ("-inf", None),
        ("nan", None),
        ("-1", None),
        ("soon", None),
    ],
)
def test_retry_after_accepts_only_finite_non_negative(
    raw: str, expected: float | None
) -> None:
    assert retry_after({"retry-after": raw}) == expected


def test_retry_after_absent() -> None:
    assert retry_after({}) is None


def test_timeout_is_checked_before_connection() -> None:
    # Both SDKs make the timeout error a subclass of the connection error.
    err = map_sdk_error(
        ProviderName.OPENAI, openai.APITimeoutError(HTTP_REQUEST), ERRORS
    )
    assert err.kind is LLMErrorKind.TIMEOUT


def test_connection_maps_to_retryable_connection() -> None:
    err = map_sdk_error(
        ProviderName.OPENAI,
        openai.APIConnectionError(request=HTTP_REQUEST),
        ERRORS,
    )
    assert err.kind is LLMErrorKind.CONNECTION
    assert err.retryable is True


def test_status_error_with_infinite_retry_after() -> None:
    response = httpx2.Response(
        429, headers={"retry-after": "inf"}, request=HTTP_REQUEST
    )
    exc = openai.RateLimitError("SECRET", response=response, body=None)
    err = map_sdk_error(ProviderName.OPENAI, exc, ERRORS)
    assert err.kind is LLMErrorKind.RATE_LIMIT
    assert err.retry_after_seconds is None
    assert "SECRET" not in str(err)


def test_unknown_exception_names_only_the_class() -> None:
    err = map_sdk_error(
        ProviderName.OPENAI, RuntimeError("SECRET-PROMPT"), ERRORS
    )
    assert err.kind is LLMErrorKind.UNKNOWN
    assert "SECRET-PROMPT" not in str(err)


def test_log_call_never_logs_the_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    log_call(
        ProviderName.OPENAI,
        LLMRequestSchema(model="m", prompt="SECRET-PROMPT"),
        "success",
        0.0,
    )
    assert "outcome=success" in caplog.text
    assert "SECRET-PROMPT" not in caplog.text

from collections.abc import Mapping

from components.third_party.llm.enums import LLMErrorKind


def retry_after(headers: Mapping[str, str]) -> float | None:
    """Seconds from a ``retry-after`` response header, or None when absent,
    non-numeric or negative."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


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

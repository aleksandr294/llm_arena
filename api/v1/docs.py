"""OpenAPI documentation helpers: the shared error responses.

Documentation only; nothing here changes runtime behavior. 422 is not
listed here because FastAPI adds ``HTTPValidationError`` itself to every
operation that has parameters or a body.
"""

from typing import Any

from components.core.schemas import ErrorSchema

_DESCRIPTIONS = {
    401: "Missing, invalid or expired credentials.",
    403: "The caller's role or organization does not allow this.",
    404: "The resource does not exist or is not visible to the caller.",
    409: "The request conflicts with the current state.",
    503: "A dependency is failing.",
}


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """``responses`` entries for the given error status codes, all with the
    common ``ErrorSchema`` body."""
    return {
        code: {"model": ErrorSchema, "description": _DESCRIPTIONS[code]}
        for code in codes
    }

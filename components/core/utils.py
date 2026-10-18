"""Helpers shared across components."""

from typing import Any


def reject_nul(value: str) -> str:
    # PostgreSQL text cannot hold NUL; without this the insert or lookup
    # fails with a 500 instead of a validation error.
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


def reject_nul_deep(value: Any) -> Any:
    """PostgreSQL JSONB cannot store NUL in strings; fail with 422 rather
    than a database error."""
    if isinstance(value, str):
        reject_nul(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            reject_nul(str(key))
            reject_nul_deep(item)
    elif isinstance(value, list):
        for item in value:
            reject_nul_deep(item)
    return value

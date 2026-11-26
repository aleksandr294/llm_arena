"""Helpers shared across components."""

import json
from typing import Any, Final

METADATA_MAX_DEPTH: Final = 5
METADATA_MAX_KEYS: Final = 100
METADATA_MAX_BYTES: Final = 16 * 1024


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


BACKOFF_CAP_SECONDS = 30.0
BACKOFF_BASE_SECONDS = 1.0
# A provider-supplied retry delay is honored up to this; a larger one is not
# waited for (the caller fails the request instead).
RETRY_AFTER_CAP_SECONDS = 120.0


def backoff_delay(
    retry_number: int,
    retry_after_seconds: float | None = None,
    jitter_seconds: float = 0.0,
    base_seconds: float = BACKOFF_BASE_SECONDS,
    cap_seconds: float = BACKOFF_CAP_SECONDS,
    retry_after_cap_seconds: float = RETRY_AFTER_CAP_SECONDS,
) -> float:
    """Seconds to sleep before retry ``retry_number`` (0 for the first retry).

    The base delay is exponential ``base * 2**n`` capped at ``cap_seconds``,
    or the provider's retry-after capped at ``retry_after_cap_seconds``.
    Caller-supplied jitter is added after capping, so it always applies.
    """
    if retry_after_seconds is not None:
        delay = min(retry_after_cap_seconds, max(0.0, retry_after_seconds))
    else:
        delay = min(cap_seconds, base_seconds * 2 ** max(0, retry_number))
    return delay + max(0.0, jitter_seconds)


def validate_metadata_bounds(value: dict[str, Any]) -> dict[str, Any]:
    """Bound free-form JSON: nesting of at most ``METADATA_MAX_DEPTH``
    levels (the top-level object is level 1), ``METADATA_MAX_KEYS`` keys in
    total and ``METADATA_MAX_BYTES`` serialized. Depth is checked before
    recursing further, so hostile nesting cannot exhaust the stack."""
    keys = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: list[Any] = list(item.values())
            keys += len(item)
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > METADATA_MAX_DEPTH:
            raise ValueError(
                f"must be nested at most {METADATA_MAX_DEPTH} levels deep"
            )
        if keys > METADATA_MAX_KEYS:
            raise ValueError(f"must have at most {METADATA_MAX_KEYS} keys")
        stack.extend((child, depth + 1) for child in children)
    if len(json.dumps(value)) > METADATA_MAX_BYTES:
        raise ValueError(f"must be at most {METADATA_MAX_BYTES} bytes as JSON")
    return value

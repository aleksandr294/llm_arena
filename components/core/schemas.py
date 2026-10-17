"""Shared Pydantic building blocks for request schemas."""

from typing import Annotated

from pydantic import AfterValidator


def reject_nul(value: str) -> str:
    # PostgreSQL text cannot hold NUL; without this the insert or lookup
    # fails with a 500 instead of a validation error.
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


NoNulStr = Annotated[str, AfterValidator(reject_nul)]

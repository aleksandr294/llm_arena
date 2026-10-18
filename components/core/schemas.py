"""Shared Pydantic building blocks and cross-component data classes."""

import uuid
from dataclasses import dataclass
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from components.core.enums import Role
from components.core.utils import reject_nul

NoNulStr = Annotated[str, AfterValidator(reject_nul)]


class ErrorSchema(BaseModel):
    """Body of every error answer except 422, which keeps FastAPI's own
    ``HTTPValidationError`` (``detail`` is then a list of problems)."""

    detail: str = Field(description="Human-readable reason.")


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, resolved from the database on each request.
    ``organization_id`` is ``None`` only for superadmins."""

    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    role: Role

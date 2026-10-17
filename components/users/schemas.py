import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from components.auth.security import PASSWORD_MAX_LENGTH
from components.core.access import Role
from components.core.schemas import NoNulStr
from components.users.service import EMAIL_MAX_LENGTH


class UserCreateSchema(BaseModel):
    # Format and password policy are checked by the service so the rules
    # live in one place; the bounds here keep oversized input out.
    email: NoNulStr = Field(min_length=1, max_length=EMAIL_MAX_LENGTH + 64)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH * 2)
    role: Role
    organization_id: uuid.UUID | None = None


class UserUpdateSchema(BaseModel):
    role: Literal[Role.STAFF, Role.ADMIN] | None = None
    is_active: bool | None = None
    password: str | None = Field(
        default=None, min_length=1, max_length=PASSWORD_MAX_LENGTH * 2
    )

    @model_validator(mode="after")
    def _not_empty(self) -> "UserUpdateSchema":
        if not self.model_fields_set:
            raise ValueError(
                "provide at least one of role, is_active, password"
            )
        if self.model_fields_set and all(
            getattr(self, f) is None for f in self.model_fields_set
        ):
            raise ValueError("fields must not be null")
        return self


class UserSchema(BaseModel):
    """Never exposes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class UserPageSchema(BaseModel):
    items: list[UserSchema]
    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

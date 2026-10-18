import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from components.core.utils import reject_nul

OrganizationName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    AfterValidator(reject_nul),
]


class OrganizationCreateSchema(BaseModel):
    name: OrganizationName


class OrganizationUpdateSchema(BaseModel):
    name: OrganizationName | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "OrganizationUpdateSchema":
        if not self.model_fields_set:
            raise ValueError("provide at least one of name, is_active")
        if self.name is None and self.is_active is None:
            raise ValueError("name and is_active must not be null")
        return self


class OrganizationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime


class OrganizationPageSchema(BaseModel):
    items: list[OrganizationSchema]
    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

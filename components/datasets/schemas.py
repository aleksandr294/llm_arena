import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

NAME_MAX = 255
DESCRIPTION_MAX = 2000
TEXT_MAX = 50000


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


def _reject_nul_deep(value: Any) -> Any:
    """PostgreSQL JSONB cannot store NUL in strings; fail with 422 rather
    than a database error."""
    if isinstance(value, str):
        _reject_nul(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_nul(str(key))
            _reject_nul_deep(item)
    elif isinstance(value, list):
        for item in value:
            _reject_nul_deep(item)
    return value


# Stored text is kept exactly as supplied; only dataset names are trimmed.
NameStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX),
    AfterValidator(_reject_nul),
]
DescriptionStr = Annotated[
    str,
    StringConstraints(max_length=DESCRIPTION_MAX),
    AfterValidator(_reject_nul),
]
InputStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=TEXT_MAX),
    AfterValidator(_reject_nul),
]
OutputStr = Annotated[
    str, StringConstraints(max_length=TEXT_MAX), AfterValidator(_reject_nul)
]
MetadataDict = Annotated[dict[str, Any], AfterValidator(_reject_nul_deep)]


class DatasetCreateSchema(BaseModel):
    name: NameStr
    description: DescriptionStr | None = None


class DatasetSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID | None
    organization_id: uuid.UUID
    created_at: datetime


class DatasetDetailSchema(DatasetSchema):
    case_count: int


class TestCaseCreateSchema(BaseModel):
    __test__ = False  # not a pytest test class

    input: InputStr
    expected_output: OutputStr | None = None
    metadata: MetadataDict | None = None


class TestCaseSchema(BaseModel):
    __test__ = False  # not a pytest test class
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    position: int
    input: str
    expected_output: str | None
    metadata: dict[str, Any] | None = Field(
        validation_alias=AliasChoices("metadata_", "metadata")
    )
    created_at: datetime


class DatasetPageSchema(BaseModel):
    items: list[DatasetSchema]
    total: int
    limit: int
    offset: int


class TestCasePageSchema(BaseModel):
    __test__ = False  # not a pytest test class

    items: list[TestCaseSchema]
    total: int
    limit: int
    offset: int

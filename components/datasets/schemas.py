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

from components.core.utils import reject_nul, reject_nul_deep

NAME_MAX = 255
DESCRIPTION_MAX = 2000
TEXT_MAX = 50000


# Stored text is kept exactly as supplied; only dataset names are trimmed.
NameStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX),
    AfterValidator(reject_nul),
]
DescriptionStr = Annotated[
    str,
    StringConstraints(max_length=DESCRIPTION_MAX),
    AfterValidator(reject_nul),
]
InputStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=TEXT_MAX),
    AfterValidator(reject_nul),
]
OutputStr = Annotated[
    str, StringConstraints(max_length=TEXT_MAX), AfterValidator(reject_nul)
]
MetadataDict = Annotated[dict[str, Any], AfterValidator(reject_nul_deep)]


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

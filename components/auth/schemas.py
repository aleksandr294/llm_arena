import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from components.auth.security import PASSWORD_MAX_LENGTH
from components.auth.service import RejectionReason
from components.core.access import Role
from components.core.schemas import NoNulStr

# Bounds keep oversized inputs from reaching the password hasher.
_EMAIL_MAX = 254
_TOKEN_MAX = 4096


class LoginRequestSchema(BaseModel):
    email: NoNulStr = Field(min_length=1, max_length=_EMAIL_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class RefreshRequestSchema(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=_TOKEN_MAX)


class ValidateRequestSchema(BaseModel):
    token: str = Field(min_length=1, max_length=_TOKEN_MAX)


class TokenPairSchema(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class ValidationResultSchema(BaseModel):
    valid: bool
    user_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None
    role: Role | None = None
    expires_at: datetime | None = None
    reason: RejectionReason | None = None

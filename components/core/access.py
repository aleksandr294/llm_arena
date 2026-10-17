import uuid
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """User roles. Staff own their data, admins manage an organization and
    superadmins operate the platform (they belong to no organization)."""

    STAFF = "staff"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, resolved from the database on each request.
    ``organization_id`` is ``None`` only for superadmins."""

    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    role: Role

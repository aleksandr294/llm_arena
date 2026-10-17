import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import ColumnElement, false, true


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


class OrganizationRequiredError(Exception):
    """Creating data needs an organization, which superadmins do not have."""

    def __init__(self) -> None:
        super().__init__(
            "A superadmin has no organization and cannot own data; "
            "use an organization account"
        )


def require_organization(principal: Principal) -> uuid.UUID:
    """The organization new data is recorded under."""
    if principal.organization_id is None:
        raise OrganizationRequiredError
    return principal.organization_id


def scope_clause(model: Any, principal: Principal) -> ColumnElement[bool]:
    """SQL condition limiting ``model`` rows to what ``principal`` may see.

    ``model`` must have ``owner_id`` and ``organization_id`` columns. Staff
    see rows they own, admins those of their organization and superadmins
    everything. Legacy rows have no owner, so staff never match them.
    """
    match principal.role:
        case Role.STAFF:
            return cast(
                ColumnElement[bool], model.owner_id == principal.user_id
            )
        case Role.ADMIN:
            if principal.organization_id is None:
                # Cannot happen (database check); fail closed regardless.
                return false()
            return cast(
                ColumnElement[bool],
                model.organization_id == principal.organization_id,
            )
        case Role.SUPERADMIN:
            return true()
    raise ValueError(f"Unknown role: {principal.role!r}")

import uuid

from sqlalchemy import ColumnElement, false, true
from sqlalchemy.orm import InstrumentedAttribute

from components.core.database import Base
from components.core.enums import Role
from components.core.exceptions import OrganizationRequiredError
from components.core.schemas import Principal


def require_organization(principal: Principal) -> uuid.UUID:
    """The organization new data is recorded under."""
    if principal.organization_id is None:
        raise OrganizationRequiredError
    return principal.organization_id


def _column(
    model: type[Base], name: str
) -> InstrumentedAttribute[uuid.UUID | None]:
    # Models with these columns share no base class, so the lookup is by
    # name; a missing column raises AttributeError.
    column: InstrumentedAttribute[uuid.UUID | None] = getattr(model, name)
    return column


def scope_clause(
    model: type[Base], principal: Principal
) -> ColumnElement[bool]:
    """SQL condition limiting ``model`` rows to what ``principal`` may see.

    ``model`` must have ``owner_id`` and ``organization_id`` columns. Staff
    see rows they own, admins those of their organization and superadmins
    everything. Legacy rows have no owner, so staff never match them.
    """
    match principal.role:
        case Role.STAFF:
            return _column(model, "owner_id") == principal.user_id
        case Role.ADMIN:
            if principal.organization_id is None:
                # Cannot happen (database check); fail closed regardless.
                return false()
            return (
                _column(model, "organization_id") == principal.organization_id
            )
        case Role.SUPERADMIN:
            return true()
    raise ValueError(f"Unknown role: {principal.role!r}")

"""Organization blocking. Only superadmins see organizations, and the only
change they can make is flipping ``is_active``."""

import uuid
from typing import Any

from fastadmin import AdminApiException, register

from admin.core import ReadOnlyScopedAdmin, principal_of
from components.core.database import get_session_factory
from components.core.enums import Role
from components.organizations.exceptions import OrganizationNotFoundError
from components.organizations.models import Organization
from components.organizations.service import OrganizationService


@register(Organization)
class OrganizationAdmin(ReadOnlyScopedAdmin):
    list_display = ("name", "is_active", "created_at")
    search_fields = ("name",)
    ordering = ("name",)
    # Shown, but not editable; save_model ignores them regardless.
    readonly_fields = ("name", "name_lower", "created_at")

    def _is_superadmin(self) -> bool:
        principal = principal_of(self.user)
        return principal is not None and principal.role is Role.SUPERADMIN

    async def has_change_permission(self, user_id: Any = None) -> bool:
        return self._is_superadmin()

    async def save_model(
        self, id: Any, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Take ``is_active`` from the payload and nothing else, and apply it
        through ``OrganizationService.update`` so refresh tokens are revoked
        in the same transaction as for a deactivation over the REST API."""
        if id is None or not self._is_superadmin():
            raise AdminApiException(
                403, detail="You do not have permission to perform this action."
            )
        # An absent key changes nothing; a present one must be a real bool
        # (null, strings, lists and numbers are refused).
        is_active = payload.get("is_active")
        if "is_active" in payload and not isinstance(is_active, bool):
            raise AdminApiException(422, detail="is_active must be a boolean.")
        try:
            organization_id = id if isinstance(id, uuid.UUID) else uuid.UUID(id)
        except (ValueError, AttributeError, TypeError):
            return None
        # The scoped read is the visibility check (superadmins see all).
        if await self.orm_get_obj(organization_id) is None:
            return None
        async with get_session_factory()() as session:
            try:
                await OrganizationService(session).update(
                    organization_id, is_active=is_active
                )
            except OrganizationNotFoundError:
                return None
        result: dict[str, Any] | None = await self.orm_serialize_obj_by_id(
            organization_id
        )
        return result

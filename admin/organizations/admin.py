"""Organizations: only superadmins see them. They can create one (through
``OrganizationService.create``) and flip ``is_active`` on an existing one;
nothing else changes."""

import uuid
from typing import Any, ClassVar

from fastadmin import AdminApiException, register
from fastadmin.models.schemas import WidgetType
from sqlalchemy.ext.asyncio import AsyncSession

from admin.core import ReadOnlyScopedAdmin, forbidden, principal_of
from components.core.database import session_scope
from components.core.enums import Role
from components.organizations.dependencies import build_organization_service
from components.organizations.exceptions import (
    DuplicateOrganizationNameError,
    OrganizationNotFoundError,
)
from components.organizations.models import Organization
from components.organizations.schemas import OrganizationCreateSchema
from components.users.repository import UserRepository


@register(Organization)
class OrganizationAdmin(ReadOnlyScopedAdmin):
    list_display = ("name", "is_active", "created_at")
    search_fields = ("name",)
    ordering = ("name",)
    # Shown, but not editable; save_model ignores them regardless. ``name``
    # is not listed because the add form needs it enabled; a change request
    # that carries a name is ignored (see ``save_model``).
    readonly_fields = ("name_lower", "created_at")
    computed_fields: ClassVar[dict[str, WidgetType]] = {
        "user_count": WidgetType.InputNumber
    }

    async def computed_values(
        self, session: AsyncSession, obj: Any
    ) -> dict[str, Any]:
        return {
            "user_count": await UserRepository(session).count_by_organization(
                obj.id
            )
        }

    def _is_superadmin(self) -> bool:
        principal = principal_of(self.user)
        return principal is not None and principal.role is Role.SUPERADMIN

    async def has_add_permission(self, user_id: Any = None) -> bool:
        return self._is_superadmin()

    async def has_change_permission(self, user_id: Any = None) -> bool:
        return self._is_superadmin()

    async def _create(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Only ``name`` is taken from the payload; ``is_active``, ids and
        every other key are ignored (a new organization is active)."""
        name = payload.get("name")
        try:
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            data = OrganizationCreateSchema(name=name)
        except ValueError:  # includes pydantic's ValidationError
            # Pydantic's message quotes the input; say only which field.
            raise AdminApiException(422, detail="name is not valid.") from None
        try:
            async with session_scope() as session:
                organization = await build_organization_service(session).create(
                    data.name
                )
                organization_id = organization.id
        except DuplicateOrganizationNameError as exc:
            raise AdminApiException(409, detail=str(exc)) from None
        result: dict[str, Any] | None = await self.orm_serialize_obj_by_id(
            organization_id
        )
        return result

    async def save_model(
        self, id: Any, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create: take ``name`` only. Change: take ``is_active`` from the
        payload and nothing else, and apply it
        through ``OrganizationService.update`` so refresh tokens are revoked
        in the same transaction as for a deactivation over the REST API."""
        if not self._is_superadmin():
            raise forbidden()
        if id is None:
            return await self._create(payload)
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
        try:
            # The scope commits on exit, before the read below uses another
            # session.
            async with session_scope() as session:
                await build_organization_service(session).update(
                    organization_id, is_active=is_active
                )
        except OrganizationNotFoundError:
            return None
        result: dict[str, Any] | None = await self.orm_serialize_obj_by_id(
            organization_id
        )
        return result

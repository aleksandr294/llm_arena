"""The panel's sign-in model: users of the platform.

fastadmin authenticates through this admin class and calls ``get_obj`` for
the session user on every request, so the checks that end a session
immediately (user or organization deactivated) live here.
"""

import uuid
from contextvars import ContextVar
from typing import Any, ClassVar

from fastadmin import AdminApiException, action, register
from fastadmin.models.schemas import (
    ActionResponseSchema,
    ActionResponseType,
    ModelFieldWidgetSchema,
    WidgetType,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.core import ReadOnlyScopedAdmin, forbidden, principal_of
from admin.schemas import SessionUser
from components.auth.dependencies import build_auth_service
from components.auth.repository import RefreshTokenRepository
from components.auth.security import (
    PASSWORD_MAX_LENGTH,
    verify_dummy_password,
    verify_password,
)
from components.core.config import get_settings
from components.core.database import session_scope
from components.core.enums import Role
from components.core.schemas import Principal
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository
from components.users.dependencies import build_user_service
from components.users.exceptions import (
    DuplicateEmailError,
    UserConflictError,
    UserForbiddenError,
    UserInputError,
    UserNotFoundError,
)
from components.users.models import User
from components.users.repository import UserRepository
from components.users.schemas import UserCreateSchema


async def _organization_active(session: AsyncSession, user: User) -> bool:
    if user.organization_id is None:
        return True  # superadmins belong to no organization
    organization = await OrganizationRepository(session).get(
        user.organization_id
    )
    return organization is not None and organization.is_active


async def _active_principal(
    session: AsyncSession, user: User | None
) -> Principal | None:
    """The principal of an active user of an active organization."""
    if user is None or not user.is_active:
        return None
    if not await _organization_active(session, user):
        return None
    try:
        return Principal(user.id, user.organization_id, Role(user.role))
    except ValueError:
        return None  # unknown role: fail closed


# Set when fastadmin binds a request context to the admin classes, which it
# does (with the user it looked up, possibly ``None``) only after session
# validation and before any data route runs.
_context_bound: ContextVar[bool] = ContextVar(
    "panel_user_context_bound", default=False
)


# Form-only inputs of the add form. fastadmin builds forms from model
# columns and ``password_hash`` (excluded) and the organization foreign key
# are not among them, so both are synthetic fields: accepted by
# ``save_model`` on create, never serialized, filtered or sorted by. The
# initial password is deliberately NOT the ``password_hash`` column name, so
# no code path can confuse the input with the stored hash.
PASSWORD_FIELD = "password"
ORGANIZATION_FIELD = "organization_id"
FORM_ONLY_FIELDS = frozenset({PASSWORD_FIELD, ORGANIZATION_FIELD})
# Upper bound on ids of one bulk action.
MAX_ACTION_IDS = 500


@register(User)
class UserAdmin(ReadOnlyScopedAdmin):
    # The hash must never leave the server; ``email_lower`` is a duplicate.
    exclude = ("password_hash", "email_lower")
    actions = ("revoke_refresh_tokens",)
    # Shown, never writable.
    readonly_fields = ("created_at",)
    list_display = ("email", "role", "is_active", "created_at")
    search_fields = ("email",)
    ordering = ("-created_at",)
    form_only_fields = FORM_ONLY_FIELDS
    computed_fields: ClassVar[dict[str, WidgetType]] = {
        "organization_name": WidgetType.Input,
        "active_refresh_tokens": WidgetType.InputNumber,
    }

    async def computed_values(
        self, session: AsyncSession, obj: Any
    ) -> dict[str, Any]:
        # Only the organization name and a count: never a token or hash.
        # Organizations are visible to superadmins only, so for others the
        # name is absent.
        name = None
        if obj.organization_id is not None:
            name = await session.scalar(
                select(Organization.name).where(
                    Organization.id == obj.organization_id
                )
            )
        return {
            "organization_name": name,
            "active_refresh_tokens": await RefreshTokenRepository(
                session
            ).count_active_for_user(obj.id),
        }

    # --- add form -----------------------------------------------------------

    def get_model_fields_with_widget_types(
        self, with_m2m: bool | None = None
    ) -> list[ModelFieldWidgetSchema]:
        fields: list[ModelFieldWidgetSchema] = (
            super().get_model_fields_with_widget_types(with_m2m=with_m2m)
        )
        if with_m2m:
            return fields
        for field in fields:
            # Server-set values: never required in a form.
            if field.name in ("created_at", "is_active"):
                field.form_widget_props = {
                    **field.form_widget_props,
                    "required": False,
                }
        return [
            *fields,
            ModelFieldWidgetSchema(
                name=PASSWORD_FIELD,
                column_name=PASSWORD_FIELD,
                is_m2m=False,
                is_pk=False,
                is_immutable=False,
                form_widget_type=WidgetType.PasswordInput,
                form_widget_props={
                    "required": True,
                    "label": "Initial password (new users only)",
                },
                filter_widget_type=WidgetType.Input,
                filter_widget_props={"required": False},
            ),
            ModelFieldWidgetSchema(
                name=ORGANIZATION_FIELD,
                column_name=ORGANIZATION_FIELD,
                is_m2m=False,
                is_pk=False,
                is_immutable=False,
                form_widget_type=WidgetType.Input,
                form_widget_props={
                    "required": False,
                    "label": "Organization id (superadmin only)",
                },
                filter_widget_type=WidgetType.Input,
                filter_widget_props={"required": False},
            ),
        ]

    def get_fields_for_serialize(self) -> set[str]:
        # The schema generator drops fields that are not serializable, so
        # the form-only inputs must be listed; ``serialize_obj_attributes``
        # then never reads them.
        names: set[str] = super().get_fields_for_serialize()
        return names | FORM_ONLY_FIELDS

    def get_writable_field_names(self) -> set[str]:
        names: set[str] = super().get_writable_field_names()
        return names - FORM_ONLY_FIELDS

    async def serialize_obj_attributes(
        self,
        obj: Any,
        attributes_to_serizalize: list[ModelFieldWidgetSchema],
        list_view: bool = False,
    ) -> dict[str, Any]:
        data: dict[str, Any] = await super().serialize_obj_attributes(
            obj,
            [
                f
                for f in attributes_to_serizalize
                if f.name not in FORM_ONLY_FIELDS
            ],
            list_view=list_view,
        )
        return data

    # --- permissions --------------------------------------------------------

    def _may_manage(self) -> bool:
        principal = principal_of(self.user)
        return principal is not None and principal.role in (
            Role.ADMIN,
            Role.SUPERADMIN,
        )

    async def has_add_permission(self, user_id: Any = None) -> bool:
        return self._may_manage()

    async def has_change_permission(self, user_id: Any = None) -> bool:
        return self._may_manage()

    async def has_action_permission(self, user_id: Any = None) -> bool:
        return self._may_manage()

    # --- writes -------------------------------------------------------------

    async def save_model(
        self, id: Any, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create (``id`` is None) or block/unblock. Only whitelisted keys
        are read from the payload; everything is decided by ``UserService``
        for the signed-in principal. No error message echoes a password."""
        caller = self.caller()
        if not self._may_manage():
            raise forbidden()
        try:
            user_id: uuid.UUID | None
            if id is None:
                user_id = await self._create(caller, payload)
            else:
                user_id = await self._set_active(caller, id, payload)
        except UserForbiddenError:
            raise forbidden() from None
        except DuplicateEmailError as exc:
            raise AdminApiException(409, detail=str(exc)) from None
        except UserConflictError as exc:
            raise AdminApiException(409, detail=str(exc)) from None
        except UserInputError as exc:
            raise AdminApiException(422, detail=f"{exc.field}: {exc}") from None
        except UserNotFoundError:
            return None
        if user_id is None:
            return None
        result: dict[str, Any] | None = await self.orm_serialize_obj_by_id(
            user_id
        )
        return result

    async def _create(
        self, caller: Principal, payload: dict[str, Any]
    ) -> uuid.UUID:
        organization_id = payload.get(ORGANIZATION_FIELD)
        try:
            data = UserCreateSchema(
                email=payload.get("email"),
                password=payload.get(PASSWORD_FIELD),
                role=payload.get("role"),
                organization_id=organization_id or None,
            )
        except ValidationError as exc:
            # Pydantic quotes the input (the password included): name only
            # the offending fields.
            fields = sorted({str(e["loc"][0]) for e in exc.errors()})
            raise AdminApiException(
                422, detail=f"Invalid value for: {', '.join(fields)}."
            ) from None
        async with session_scope() as session:
            service = build_user_service(
                session, build_auth_service(session, get_settings())
            )
            user = await service.create_user(
                caller,
                data.email,
                data.password,
                data.role,
                data.organization_id,
            )
            return user.id

    async def _set_active(
        self, caller: Principal, id: Any, payload: dict[str, Any]
    ) -> uuid.UUID | None:
        try:
            user_id = id if isinstance(id, uuid.UUID) else uuid.UUID(str(id))
        except ValueError:
            return None
        # The scoped read is the visibility check: out of scope is "not
        # found", the same answer as for a missing user.
        if await self.orm_get_obj(user_id) is None:
            return None
        is_active = payload.get("is_active")
        if "is_active" not in payload:
            return user_id
        if not isinstance(is_active, bool):
            raise AdminApiException(422, detail="is_active must be a boolean.")
        async with session_scope() as session:
            service = build_user_service(
                session, build_auth_service(session, get_settings())
            )
            await service.update_user(caller, user_id, is_active=is_active)
        return user_id

    @action(description="Revoke refresh tokens")  # type: ignore[untyped-decorator]
    async def revoke_refresh_tokens(
        self, ids: list[Any]
    ) -> ActionResponseSchema:
        """End every refresh token of the selected users, leaving their
        active state and password alone. Ids the caller cannot see are
        skipped, and the answer is the same whatever was selected."""
        self.caller()
        if not self._may_manage():
            raise forbidden()
        if len(ids) > MAX_ACTION_IDS:
            raise AdminApiException(422, detail="Too many users selected.")
        wanted: set[uuid.UUID] = set()
        for raw in ids:
            try:
                wanted.add(
                    raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
                )
            except ValueError:
                continue
        if wanted:
            async with self.get_sessionmaker()() as scoped:
                visible = list(
                    await scoped.scalars(
                        select(User.id).where(User.id.in_(wanted))
                    )
                )
            async with session_scope() as session:
                auth = build_auth_service(session, get_settings())
                for user_id in visible:
                    await auth.revoke_all_sessions(user_id)
        return ActionResponseSchema(
            type=ActionResponseType.MESSAGE,
            data="Refresh token revocation done for the permitted users.",
        )

    async def authenticate(
        self, username: str, password: str
    ) -> uuid.UUID | None:
        """Id of the active user of an active organization whose password
        matches. Every failure is the same ``None``: the caller learns
        neither whether the email exists nor why sign-in failed."""
        # An unscoped, short session: nobody is signed in yet, and the
        # connection is released before the slow Argon2id verification.
        async with session_scope() as session:
            user = await UserRepository(session).get_by_email(username.strip())
            password_hash = user.password_hash if user is not None else None
            principal = await _active_principal(session, user)

        if password_hash is None or len(password) > PASSWORD_MAX_LENGTH:
            await verify_dummy_password(password)
            return None
        if not await verify_password(password_hash, password):
            return None
        return principal.user_id if principal is not None else None

    def set_context(
        self, request: Any | None = None, user: Any | None = None
    ) -> None:
        _context_bound.set(True)
        super().set_context(request=request, user=user)

    async def get_obj(self, id: Any) -> Any:
        """Two callers share this method.

        With no signed-in user bound (``self.user`` is ``None``) it is
        fastadmin validating a session cookie: an unscoped read of exactly
        that user, returning a ``SessionUser`` (or ``None`` when the
        user or their organization is inactive, which makes the session
        invalid at once). fastadmin then binds the returned value as
        ``self.user`` on every admin class, which is how the scoped sessions
        learn who is asking.

        With a user bound it is a data route (``/me``, ``/retrieve/User``)
        and returns the serialized row, read through the scoped session.
        fastadmin looks the user up twice per request, then binds the result
        (possibly ``None``) with ``set_context``. If the second lookup comes
        back empty, the data route sees no user but a bound context; that is
        answered with ``None`` (404) instead of the unscoped read, which
        would return a principal for an arbitrary id.

        Guarantee this relies on: fastadmin keeps the bound user in context
        variables, so a request must never see another request's binding.
        ASGI servers run each request in its own task (uvicorn does, even
        on one keep-alive connection), and a task's context changes do not
        propagate back, so validation always starts unbound. If a binding
        did leak, the damage is limited: it cannot grant access to rows
        (the scope comes from the bound ``Principal`` of that request only),
        but a leaked superadmin binding would make a deactivated user's
        session validate. ``tests/admin/test_panel_server.py`` pins the
        guarantee against a real uvicorn server. Do not call into the panel
        from a long-lived shared task or a middleware that reuses a context.
        """
        if self.user is not None:
            return await super().get_obj(id)
        if _context_bound.get():
            # A data route whose user lookup came back empty (the user was
            # deactivated between fastadmin's two lookups): never fall back
            # to the unscoped session-validating read below.
            return None
        try:
            user_id = id if isinstance(id, uuid.UUID) else uuid.UUID(str(id))
        except ValueError:
            return None
        async with session_scope() as session:
            user = await UserRepository(session).get(user_id)
            principal = await _active_principal(session, user)
        # Never the bare principal: see ``SessionUser``.
        return SessionUser(principal) if principal is not None else None

    async def change_password(self, id: Any, password: str) -> None:
        # ValueError is what fastadmin turns into a plain 404.
        raise ValueError("passwords cannot be changed in the panel")

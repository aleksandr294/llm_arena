"""The panel's sign-in model: users of the platform.

fastadmin authenticates through this admin class and calls ``get_obj`` for
the session user on every request, so the checks that end a session
immediately (user or organization deactivated) live here.
"""

import uuid
from contextvars import ContextVar
from typing import Any

from fastadmin import register
from sqlalchemy.ext.asyncio import AsyncSession

from admin.core import ReadOnlyScopedAdmin, SessionUser
from components.auth.security import (
    PASSWORD_MAX_LENGTH,
    verify_dummy_password,
    verify_password,
)
from components.core.access import Principal, Role
from components.core.database import get_session_factory
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository


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


@register(User)
class UserAdmin(ReadOnlyScopedAdmin):
    # The hash must never leave the server; ``email_lower`` is a duplicate.
    exclude = ("password_hash", "email_lower")
    list_display = ("email", "role", "is_active", "created_at")
    search_fields = ("email",)
    ordering = ("-created_at",)

    async def authenticate(
        self, username: str, password: str
    ) -> uuid.UUID | None:
        """Id of the active user of an active organization whose password
        matches. Every failure is the same ``None``: the caller learns
        neither whether the email exists nor why sign-in failed."""
        # An unscoped, short session: nobody is signed in yet, and the
        # connection is released before the slow Argon2id verification.
        async with get_session_factory()() as session:
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
        async with get_session_factory()() as session:
            user = await UserRepository(session).get(user_id)
            principal = await _active_principal(session, user)
        # Never the bare principal: see ``SessionUser``.
        return SessionUser(principal) if principal is not None else None

    async def change_password(self, id: Any, password: str) -> None:
        # ValueError is what fastadmin turns into a plain 404.
        raise ValueError("passwords cannot be changed in the panel")

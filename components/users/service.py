"""User use cases: bootstrap (CLI only) and role-checked management."""

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.exceptions import PasswordPolicyError
from components.auth.security import hash_password, validate_password_policy
from components.auth.service import AuthService
from components.core.enums import Role
from components.core.schemas import Principal
from components.organizations.repository import OrganizationRepository
from components.users.exceptions import (
    DuplicateEmailError,
    InvalidEmailError,
    UserConflictError,
    UserForbiddenError,
    UserInputError,
    UserNotFoundError,
)
from components.users.models import User
from components.users.repository import UserRepository
from components.users.utils import normalize_email


class UserService:
    """Uses the caller's session and never commits; the entry point owns the
    transaction. ``auth_service`` ends a user's sessions on deactivation or
    a password change."""

    def __init__(
        self, session: AsyncSession, auth_service: AuthService
    ) -> None:
        self.session = session
        self.auth = auth_service
        self.users = UserRepository(session)
        self.organizations = OrganizationRepository(session)

    async def create_superadmin(self, email: str, password: str) -> User:
        """Raises ``InvalidEmailError``, ``PasswordPolicyError`` or
        ``DuplicateEmailError``. Not reachable over HTTP."""
        email = normalize_email(email)
        validate_password_policy(password)
        if await self.users.get_by_email(email) is not None:
            raise DuplicateEmailError
        password_hash = await hash_password(password)
        try:
            user = await self.users.create(
                email, password_hash, Role.SUPERADMIN, None
            )
        except IntegrityError:
            # Lost a race against a concurrent insert of the same email.
            await self.session.rollback()
            raise DuplicateEmailError from None
        return user

    # --- management (HTTP) --------------------------------------------------

    async def create_user(
        self,
        caller: Principal,
        email: str,
        password: str,
        role: Role,
        organization_id: uuid.UUID | None,
    ) -> User:
        """Raises ``UserForbiddenError``, ``UserInputError`` or
        ``DuplicateEmailError``. Superadmins are never created over HTTP."""
        if caller.role is Role.STAFF:
            raise UserForbiddenError
        if role is Role.SUPERADMIN:
            if caller.role is not Role.SUPERADMIN:
                raise UserForbiddenError
            if organization_id is not None:
                raise UserInputError(
                    "organization_id", "a superadmin has no organization"
                )
            raise UserForbiddenError
        if caller.role is Role.ADMIN:
            if organization_id not in (None, caller.organization_id):
                raise UserForbiddenError
            organization_id = caller.organization_id
        elif organization_id is None:
            raise UserInputError(
                "organization_id", "organization_id is required"
            )
        email = self._email(email)
        self._password(password)
        if organization_id is None or not await self.organizations.exists(
            organization_id
        ):
            raise UserInputError("organization_id", "organization not found")
        if await self.users.get_by_email(email) is not None:
            raise DuplicateEmailError
        password_hash = await hash_password(password)
        try:
            user = await self.users.create(
                email, password_hash, role, organization_id
            )
        except IntegrityError:
            await self.session.rollback()
            raise DuplicateEmailError from None
        return user

    async def list_users(
        self,
        caller: Principal,
        limit: int,
        offset: int,
        organization_id: uuid.UUID | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
    ) -> tuple[Sequence[User], int]:
        user_id = None
        if caller.role is Role.STAFF:
            user_id = caller.user_id
        elif caller.role is Role.ADMIN:
            if organization_id not in (None, caller.organization_id):
                return [], 0
            organization_id = caller.organization_id
        return await self.users.list_page(
            limit, offset, organization_id, role, is_active, user_id
        )

    async def get_user(self, caller: Principal, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id)
        if user is None or not self._in_scope(caller, user):
            raise UserNotFoundError
        return user

    async def update_user(
        self,
        caller: Principal,
        user_id: uuid.UUID,
        role: Role | None = None,
        is_active: bool | None = None,
        password: str | None = None,
    ) -> User:
        """Raises ``UserForbiddenError``, ``UserNotFoundError``,
        ``UserConflictError`` or ``UserInputError``. Deactivation and a
        password change end the user's sessions in the same transaction."""
        if caller.role is Role.STAFF:
            raise UserForbiddenError
        user = await self.get_user(caller, user_id)
        if user.role == Role.SUPERADMIN:
            raise UserForbiddenError
        if user.id == caller.user_id:
            if role is not None and role.value != user.role:
                raise UserConflictError("you cannot change your own role")
            if is_active is False:
                raise UserConflictError("you cannot deactivate yourself")
        if password is not None:
            self._password(password)
            user.password_hash = await hash_password(password)
        if role is not None:
            user.role = role.value
        if is_active is not None:
            user.is_active = is_active
        if password is not None or is_active is False:
            await self.auth.revoke_all_sessions(user.id)
        return user

    @staticmethod
    def _in_scope(caller: Principal, user: User) -> bool:
        if caller.role is Role.SUPERADMIN:
            return True
        if caller.role is Role.ADMIN:
            return user.organization_id == caller.organization_id
        return user.id == caller.user_id

    @staticmethod
    def _email(email: str) -> str:
        try:
            return normalize_email(email)
        except InvalidEmailError as exc:
            raise UserInputError("email", str(exc)) from None

    @staticmethod
    def _password(password: str) -> None:
        try:
            validate_password_policy(password)
        except PasswordPolicyError as exc:
            raise UserInputError("password", str(exc)) from None

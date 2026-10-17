"""User use cases: bootstrap (CLI only) and role-checked management."""

import re
import uuid
from collections.abc import Sequence
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.security import (
    PasswordPolicyError,
    hash_password,
    validate_password_policy,
)
from components.auth.service import AuthService
from components.core.access import Principal, Role
from components.core.config import SettingsSchema
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository

EMAIL_MAX_LENGTH: Final = 254
# Deliberately simple: one "@", a dotted domain, printable ASCII only (no
# whitespace, controls or non-ASCII). ASCII keeps ``lower()`` length-stable
# and free of locale surprises. Deliverability is not checked.
_LOCAL: Final = r"[\x21-\x3f\x41-\x7e]+"
_LABEL: Final = r"[\x21-\x2d\x2f-\x3f\x41-\x7e]+"
_EMAIL_PATTERN: Final = re.compile(rf"{_LOCAL}@{_LABEL}(\.{_LABEL})+")


class InvalidEmailError(ValueError):
    pass


class DuplicateEmailError(Exception):
    def __init__(self) -> None:
        super().__init__("a user with this email already exists")


class UserForbiddenError(Exception):
    def __init__(self) -> None:
        super().__init__("forbidden")


class UserNotFoundError(Exception):
    """Also raised for users outside the caller's scope, so their
    existence is not disclosed."""

    def __init__(self) -> None:
        super().__init__("user not found")


class UserConflictError(Exception):
    """The change would lock the caller out of their own account."""


class UserInputError(ValueError):
    """Invalid input; ``field`` names the offending request field."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def normalize_email(email: str) -> str:
    """Trim and validate. Case is preserved (uniqueness ignores it)."""
    value = email.strip()
    if (
        "\x00" in value
        or len(value) > EMAIL_MAX_LENGTH
        # The stored ``email_lower`` column has the same bound.
        or len(value.lower()) > EMAIL_MAX_LENGTH
        or not _EMAIL_PATTERN.fullmatch(value)
    ):
        raise InvalidEmailError("email is not valid")
    return value


class UserService:
    """Owns the transaction of each operation (commits on success)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

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
            await self.session.commit()
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
        if organization_id is None or not await OrganizationRepository(
            self.session
        ).exists(organization_id):
            raise UserInputError("organization_id", "organization not found")
        if await self.users.get_by_email(email) is not None:
            raise DuplicateEmailError
        password_hash = await hash_password(password)
        try:
            user = await self.users.create(
                email, password_hash, role, organization_id
            )
            await self.session.commit()
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
        settings: SettingsSchema,
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
            await AuthService(self.session, settings).revoke_all_sessions(
                user.id
            )
        await self.session.commit()
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

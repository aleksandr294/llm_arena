"""Authentication use cases. No HTTP knowledge: failures are domain errors
whose messages never contain tokens, hashes, passwords or emails."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.repository import RefreshTokenRepository
from components.auth.security import (
    ExpiredTokenError,
    InvalidTokenError,
    IssuedToken,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token_id,
    password_needs_rehash,
    verify_dummy_password,
    verify_password,
)
from components.core.config import SettingsSchema
from components.core.enums import Role
from components.core.schemas import Principal
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository


class InvalidCredentialsError(Exception):
    """Unknown email, wrong password, or inactive user or organization."""

    def __init__(self) -> None:
        super().__init__("invalid credentials")


class InvalidRefreshTokenError(Exception):
    """Refresh token is malformed, expired, revoked, used or unknown."""

    def __init__(self) -> None:
        super().__init__("invalid refresh token")


class RejectionReason(StrEnum):
    EXPIRED = "expired"
    INVALID = "invalid"
    REVOKED = "revoked"


class AccessTokenRejectedError(Exception):
    def __init__(self, reason: RejectionReason) -> None:
        super().__init__(f"access token rejected: {reason.value}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    user_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None
    role: Role | None = None
    expires_at: datetime | None = None
    reason: RejectionReason | None = None


class AuthService:
    """Owns the transaction: each public operation commits its own work, so
    reuse detection persists the family revocation before failing."""

    def __init__(self, session: AsyncSession, settings: SettingsSchema) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.organizations = OrganizationRepository(session)
        self.tokens = RefreshTokenRepository(session)

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self.users.get_by_email(email.strip())
        if user is None:
            await verify_dummy_password(password)
            raise InvalidCredentialsError
        if not await verify_password(user.password_hash, password):
            raise InvalidCredentialsError
        if not await self._is_active(user):
            raise InvalidCredentialsError
        if password_needs_rehash(user.password_hash):
            # Conditional: a concurrent password change must win.
            await self.users.replace_password_hash(
                user.id, user.password_hash, await hash_password(password)
            )
        pair = await self._issue_pair(user.id, uuid.uuid4())
        await self.session.commit()
        return pair

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            claims = decode_token(
                self.settings, refresh_token, TokenType.REFRESH
            )
        except (ExpiredTokenError, InvalidTokenError):
            raise InvalidRefreshTokenError from None
        record = await self.tokens.get_by_hash(hash_token_id(claims.jti))
        if (
            record is None
            or record.user_id != claims.user_id
            or record.family_id != claims.family_id
            or record.revoked_at is not None
            or record.expires_at <= datetime.now(UTC)
        ):
            raise InvalidRefreshTokenError
        if record.used_at is not None or not await self.tokens.mark_used(
            record.id
        ):
            # A used token presented again: assume theft, end the login.
            await self.tokens.revoke_family(record.family_id)
            await self.session.commit()
            raise InvalidRefreshTokenError
        user = await self.users.get(record.user_id)
        if user is None or not await self._is_active(user):
            await self.tokens.revoke_family(record.family_id)
            await self.session.commit()
            raise InvalidRefreshTokenError
        pair = await self._issue_pair(user.id, record.family_id)
        await self.session.commit()
        return pair

    async def logout(self, refresh_token: str) -> None:
        """Revoke the login of a refresh token. Idempotent: an already
        revoked, expired or unknown (but correctly signed) token succeeds."""
        try:
            # Expiry is not checked: an expired token must still be able to
            # end its login.
            claims = decode_token(
                self.settings,
                refresh_token,
                TokenType.REFRESH,
                verify_expiry=False,
            )
        except (ExpiredTokenError, InvalidTokenError):
            raise InvalidRefreshTokenError from None
        record = await self.tokens.get_by_hash(hash_token_id(claims.jti))
        if record is not None and record.user_id == claims.user_id:
            await self.tokens.revoke_family(record.family_id)
            await self.session.commit()

    async def authenticate(self, access_token: str) -> Principal:
        """Resolve the caller from the database, not from token claims."""
        principal, _ = await self._resolve(access_token)
        return principal

    async def validate(self, token: str) -> ValidationResult:
        try:
            principal, expires_at = await self._resolve(token)
        except AccessTokenRejectedError as exc:
            return ValidationResult(valid=False, reason=exc.reason)
        return ValidationResult(
            valid=True,
            user_id=principal.user_id,
            organization_id=principal.organization_id,
            role=principal.role,
            expires_at=expires_at,
        )

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> int:
        """End every refresh token of a user. Does not commit: callers use
        it inside their own transaction (deactivation, password change)."""
        return await self.tokens.revoke_all_for_user(user_id)

    async def _resolve(self, token: str) -> tuple[Principal, datetime]:
        try:
            claims = decode_token(self.settings, token, TokenType.ACCESS)
        except ExpiredTokenError:
            raise AccessTokenRejectedError(RejectionReason.EXPIRED) from None
        except InvalidTokenError:
            raise AccessTokenRejectedError(RejectionReason.INVALID) from None
        user = await self.users.get(claims.user_id)
        if user is None:
            raise AccessTokenRejectedError(RejectionReason.INVALID)
        if not await self._is_active(user):
            raise AccessTokenRejectedError(RejectionReason.REVOKED)
        principal = Principal(
            user_id=user.id,
            organization_id=user.organization_id,
            role=Role(user.role),
        )
        return principal, claims.expires_at

    async def _is_active(self, user: User) -> bool:
        if not user.is_active:
            return False
        if user.organization_id is None:
            return True
        organization = await self.organizations.get(user.organization_id)
        return organization is not None and organization.is_active

    async def _issue_pair(
        self, user_id: uuid.UUID, family_id: uuid.UUID
    ) -> TokenPair:
        access = create_access_token(self.settings, user_id)
        refresh: IssuedToken = create_refresh_token(
            self.settings, user_id, family_id
        )
        await self.tokens.create(
            user_id, family_id, hash_token_id(refresh.jti), refresh.expires_at
        )
        return TokenPair(
            access_token=access.token,
            refresh_token=refresh.token,
            expires_in=self.settings.jwt_access_ttl_seconds,
        )

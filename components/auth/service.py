"""Authentication use cases. No HTTP knowledge: failures are domain errors
whose messages never contain tokens, hashes, passwords or emails."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.enums import RejectionReason, TokenType
from components.auth.exceptions import (
    AccessTokenRejectedError,
    AuthThrottledError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    TokenExpiredError,
    TokenInvalidError,
)
from components.auth.repository import RefreshTokenRepository
from components.auth.security import (
    IssuedToken,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token_id,
    password_needs_rehash,
    verify_dummy_password,
    verify_password,
)
from components.auth.throttle import (
    AuthThrottleService,
    ThrottleKey,
    account_key,
    address_key,
)
from components.core.config import SettingsSchema
from components.core.database import commit
from components.core.enums import Role
from components.core.logging import log_security_event
from components.core.schemas import Principal
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository


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
    """Uses the caller's session; the entry point commits once per
    operation. The exceptions are refresh-token reuse (and a dead login at
    refresh), which revokes the family, commits and then raises, and every
    recorded sign-in failure: the revocation and the failure counter must
    persist although the request fails."""

    def __init__(
        self,
        session: AsyncSession,
        settings: SettingsSchema,
        throttle: AuthThrottleService,
    ) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.organizations = OrganizationRepository(session)
        self.tokens = RefreshTokenRepository(session)
        self.throttle = throttle

    async def login(
        self,
        email: str,
        password: str,
        client_address: str | None = None,
    ) -> TokenPair:
        """Raises ``InvalidCredentialsError`` or ``AuthThrottledError``.
        Locked accounts and addresses are refused before any password is
        checked, so even the correct password fails while locked."""
        email = email.strip()
        account = account_key(email)
        addresses = self._address_keys(client_address)
        await self._ensure_allowed(addresses, client_address)
        # The attempt is counted (atomically) and committed before the slow
        # password check, so a parallel burst cannot verify more guesses than
        # the threshold; a success resets the counter below.
        try:
            await self.throttle.begin_attempt(account)
        except AuthThrottledError:
            self._log_locked_out(client_address)
            raise
        await commit(self.session)
        try:
            pair = await self._login(email, password)
        except InvalidCredentialsError:
            log_security_event(
                "auth.login_failed",
                client_address=client_address,
                outcome="denied",
                reason="invalid_credentials",
            )
            await self._persist_failure(addresses)
            raise
        await self.throttle.reset(account)
        return pair

    async def _login(self, email: str, password: str) -> TokenPair:
        user = await self.users.get_by_email(email)
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
        return await self._issue_pair(user.id, uuid.uuid4())

    async def refresh(
        self, refresh_token: str, client_address: str | None = None
    ) -> TokenPair:
        """Raises ``InvalidRefreshTokenError`` or ``AuthThrottledError``.
        Presenting the same token twice (a client that submits a refresh
        twice at once included) is treated as theft and ends the login: a
        grace window would weaken that detection."""
        keys = self._address_keys(client_address)
        await self._ensure_allowed(keys, client_address)
        try:
            return await self._refresh(refresh_token)
        except InvalidRefreshTokenError:
            await self._persist_failure(keys)
            raise

    async def _refresh(self, refresh_token: str) -> TokenPair:
        try:
            claims = decode_token(
                self.settings, refresh_token, TokenType.REFRESH
            )
        except (TokenExpiredError, TokenInvalidError):
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
        # The user row first (lock order: user, then tokens), so a
        # deactivation either commits before this refresh looks at the user
        # or waits for it and then revokes the tokens issued here.
        user = await self.users.get_for_update(record.user_id)
        # Checked before the reuse test: when a deactivation won the race,
        # its revocation makes ``mark_used`` fail, and that must not be
        # reported as theft.
        if user is None or not await self._is_active(user):
            await self.tokens.revoke_family(record.family_id)
            # Persist-then-reject: the single commit of this operation.
            await commit(self.session)
            raise InvalidRefreshTokenError
        if record.used_at is not None or not await self.tokens.mark_used(
            record.id
        ):
            # A used token presented again: assume theft, end the login.
            log_security_event(
                "auth.refresh_reuse",
                user_id=record.user_id,
                outcome="denied",
                reason="token_reused",
            )
            await self.tokens.revoke_family(record.family_id)
            # Persist-then-reject, as above.
            await commit(self.session)
            raise InvalidRefreshTokenError
        return await self._issue_pair(user.id, record.family_id)

    async def logout(self, refresh_token: str) -> None:
        """Revoke the login of a refresh token and end the user's access
        tokens issued so far. Idempotent: an already revoked, expired or
        unknown (but correctly signed) token succeeds."""
        try:
            # Expiry is not checked: an expired token must still be able to
            # end its login.
            claims = decode_token(
                self.settings,
                refresh_token,
                TokenType.REFRESH,
                verify_expiry=False,
            )
        except (TokenExpiredError, TokenInvalidError):
            raise InvalidRefreshTokenError from None
        record = await self.tokens.get_by_hash(hash_token_id(claims.jti))
        if record is not None and record.user_id == claims.user_id:
            # User row first (lock order: user, then tokens), as refresh
            # and ``update_user`` do.
            await self.users.end_access_tokens(
                record.user_id, datetime.now(UTC)
            )
            await self.tokens.revoke_family(record.family_id)

    async def authenticate(self, access_token: str) -> Principal:
        """Resolve the caller from the database, not from token claims."""
        principal, _ = await self._resolve(access_token)
        return principal

    async def validate(
        self, token: str, client_address: str | None = None
    ) -> ValidationResult:
        """Raises ``AuthThrottledError`` for a locked client address; an
        unusable token counts as a failure for it."""
        keys = self._address_keys(client_address)
        await self._ensure_allowed(keys, client_address)
        try:
            principal, expires_at = await self._resolve(token)
        except AccessTokenRejectedError as exc:
            # An expired token is normal traffic (a gateway forwarding
            # stale tokens); only forged or revoked ones count.
            if exc.reason is not RejectionReason.EXPIRED:
                await self._persist_failure(keys)
            return ValidationResult(valid=False, reason=exc.reason)
        return ValidationResult(
            valid=True,
            user_id=principal.user_id,
            organization_id=principal.organization_id,
            role=principal.role,
            expires_at=expires_at,
        )

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> int:
        """End every refresh token and every issued access token of a user.
        Does not commit: callers use it inside their own transaction
        (deactivation, password change)."""
        await self.users.end_access_tokens(user_id, datetime.now(UTC))
        return await self.tokens.revoke_all_for_user(user_id)

    async def revoke_organization_sessions(
        self, organization_id: uuid.UUID
    ) -> int:
        """End the refresh tokens and every issued access token of the users
        of an organization (the access tokens also fail while it is
        inactive, but must not come back on reactivation). Does not
        commit."""
        await self.users.end_access_tokens_for_organization(
            organization_id, datetime.now(UTC)
        )
        return await self.tokens.revoke_all_for_organization(organization_id)

    async def purge(self, retention: timedelta) -> tuple[int, int]:
        """Delete refresh tokens expired longer than ``retention`` ago and
        throttle rows that no longer count. Returns both counts."""
        return (
            await self.tokens.purge_expired(retention),
            await self.throttle.purge_stale(),
        )

    async def _resolve(self, token: str) -> tuple[Principal, datetime]:
        try:
            claims = decode_token(self.settings, token, TokenType.ACCESS)
        except TokenExpiredError:
            raise AccessTokenRejectedError(RejectionReason.EXPIRED) from None
        except TokenInvalidError:
            raise AccessTokenRejectedError(RejectionReason.INVALID) from None
        user = await self.users.get(claims.user_id)
        if user is None:
            raise AccessTokenRejectedError(RejectionReason.INVALID)
        if not await self._is_active(user):
            raise AccessTokenRejectedError(RejectionReason.REVOKED)
        # ``iat`` has whole-second precision, so the bound is floored: a
        # token issued in the same second as the revocation survives.
        if user.tokens_valid_after is not None and (
            claims.issued_at < user.tokens_valid_after.replace(microsecond=0)
        ):
            raise AccessTokenRejectedError(RejectionReason.REVOKED)
        principal = Principal(
            user_id=user.id,
            organization_id=user.organization_id,
            role=Role(user.role),
        )
        return principal, claims.expires_at

    @staticmethod
    def _address_keys(client_address: str | None) -> list[ThrottleKey]:
        return [] if client_address is None else [address_key(client_address)]

    async def _ensure_allowed(
        self, keys: list[ThrottleKey], client_address: str | None
    ) -> None:
        try:
            await self.throttle.ensure_allowed(keys)
        except AuthThrottledError:
            self._log_locked_out(client_address)
            raise

    @staticmethod
    def _log_locked_out(client_address: str | None) -> None:
        log_security_event(
            "auth.locked_out",
            client_address=client_address,
            outcome="denied",
            reason="too_many_failures",
        )

    async def _persist_failure(self, keys: list[ThrottleKey]) -> None:
        """Count a failure and commit it: the request is about to fail, and
        a failed request rolls back, which would lose the count."""
        if keys:
            await self.throttle.record_failure(keys)
            await commit(self.session)

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

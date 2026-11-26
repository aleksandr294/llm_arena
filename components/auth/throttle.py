"""Sign-in throttling: failed attempts are counted per account and per
client address in the database, so every API process shares the counters.

Only SHA-256 hashes of the normalized email and of the address are stored.
An unknown email is counted exactly like a known one, so a lockout does not
reveal whether an account exists.
"""

import hashlib
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.enums import ThrottleScope
from components.auth.exceptions import AuthThrottledError
from components.auth.repository import AuthThrottleRepository
from components.core.config import SettingsSchema

ThrottleKey = tuple[str, str]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def account_key(
    email: str, scope: ThrottleScope = ThrottleScope.ACCOUNT
) -> ThrottleKey:
    return scope.value, _hash(email.strip().lower())


def address_key(client_address: str) -> ThrottleKey:
    return ThrottleScope.ADDRESS.value, _hash(client_address)


class AuthThrottleService:
    """Uses the caller's session and never commits: the caller commits a
    recorded failure before it rejects the request (persist-then-reject),
    because a rejected request rolls its transaction back."""

    def __init__(self, session: AsyncSession, settings: SettingsSchema) -> None:
        self.settings = settings
        self.counters = AuthThrottleRepository(session)

    async def ensure_allowed(self, keys: list[ThrottleKey]) -> None:
        """Raises ``AuthThrottledError`` when any key is locked."""
        retry_after = await self.counters.seconds_locked(keys)
        if retry_after:
            raise AuthThrottledError(retry_after)

    def _threshold(self, scope: str) -> int:
        if scope == ThrottleScope.ADDRESS.value:
            return self.settings.auth_max_failed_attempts_per_address
        return self.settings.auth_max_failed_attempts

    async def begin_attempt(self, key: ThrottleKey) -> None:
        """Count an attempt before its password is checked, atomically, and
        raise ``AuthThrottledError`` when the key is locked. The caller
        commits before the slow hash starts and resets the key on success,
        so the number of guesses in flight is bounded by the threshold
        (counting afterwards would let a parallel burst through)."""
        if not await self._count(key):
            raise AuthThrottledError(
                await self.counters.seconds_locked([key]) or 1
            )

    async def record_failure(self, keys: list[ThrottleKey]) -> None:
        # Sorted: concurrent requests lock rows in the same order.
        for key in sorted(keys):
            await self._count(key)

    async def _count(self, key: ThrottleKey) -> bool:
        scope, key_hash = key
        return await self.counters.record_failure(
            scope,
            key_hash,
            self._threshold(scope),
            timedelta(seconds=self.settings.auth_lockout_seconds),
        )

    async def reset(self, key: ThrottleKey) -> None:
        """Forget the failures of one key after a successful sign-in. Only
        the account counter is reset: the address counter must keep
        counting, or one valid login would clear an attack in progress."""
        await self.counters.reset(*key)

    async def purge_stale(self) -> int:
        return await self.counters.purge_stale(
            timedelta(seconds=self.settings.auth_lockout_seconds)
        )

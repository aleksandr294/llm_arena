import math
import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    ColumnElement,
    and_,
    case,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from components.auth.models import AuthThrottle, RefreshToken
from components.core.repository import BaseRepository
from components.users.models import User


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def create(
        self,
        user_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        return await self.add(
            RefreshToken(
                user_id=user_id,
                family_id=family_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

    async def mark_used(self, token_id: uuid.UUID) -> bool:
        """Atomically mark a token used. Returns False when it was already
        used or revoked, so concurrent refreshes cannot both succeed."""
        result = await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.id == token_id,
                RefreshToken.used_at.is_(None),
                RefreshToken.revoked_at.is_(None),
            )
            .values(used_at=func.now())
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return result.first() is not None

    async def revoke_family(self, family_id: uuid.UUID) -> int:
        """Revoke every not yet revoked token of a login. Returns the count."""
        return await self._revoke(RefreshToken.family_id == family_id)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        return await self._revoke(RefreshToken.user_id == user_id)

    async def revoke_all_for_organization(
        self, organization_id: uuid.UUID
    ) -> int:
        """Revoke the tokens of every user of an organization."""
        return await self._revoke(
            RefreshToken.user_id.in_(
                select(User.id).where(User.organization_id == organization_id)
            )
        )

    async def purge_expired(self, retention: timedelta) -> int:
        """Delete tokens that expired more than ``retention`` ago. Returns
        the count. Live tokens and recently expired ones stay (reuse
        detection needs the record of a token until it cannot be replayed)."""
        result = await self.session.execute(
            delete(RefreshToken)
            .where(RefreshToken.expires_at < func.now() - retention)
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.all())

    async def _revoke(self, condition: ColumnElement[bool]) -> int:
        result = await self.session.execute(
            update(RefreshToken)
            .where(condition, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.all())


class AuthThrottleRepository(BaseRepository[AuthThrottle]):
    """Failure counters. All times are the database clock, so every process
    agrees on a window."""

    model = AuthThrottle

    async def record_failure(
        self,
        scope: str,
        key_hash: str,
        max_failures: int,
        lockout: timedelta,
    ) -> bool:
        """Count one attempt or failure with one atomic upsert (no lost
        updates under concurrency). A window of ``lockout`` length restarts
        the count; reaching ``max_failures`` locks for ``lockout``. A key
        that is already locked is left unchanged. Returns whether it was
        counted (False: the key is locked)."""
        now = func.now()
        window_over = AuthThrottle.window_started_at + lockout <= now
        failures = case((window_over, 1), else_=AuthThrottle.failures + 1)
        upsert = insert(AuthThrottle).values(
            scope=scope,
            key_hash=key_hash,
            failures=1,
            window_started_at=now,
            locked_until=now + lockout if max_failures <= 1 else None,
        )
        statement = upsert.on_conflict_do_update(
            index_elements=[AuthThrottle.scope, AuthThrottle.key_hash],
            set_={
                "failures": failures,
                "window_started_at": case(
                    (window_over, now),
                    else_=AuthThrottle.window_started_at,
                ),
                "locked_until": case(
                    (failures >= max_failures, now + lockout),
                    (window_over, None),
                    else_=AuthThrottle.locked_until,
                ),
            },
            where=or_(
                AuthThrottle.locked_until.is_(None),
                AuthThrottle.locked_until <= now,
            ),
        ).returning(AuthThrottle.failures)
        result = await self.session.execute(statement)
        return result.first() is not None

    async def seconds_locked(self, keys: list[tuple[str, str]]) -> int:
        """Whole seconds until the longest lock among ``keys`` ends, 0 when
        none is locked."""
        if not keys:
            return 0
        remaining = await self.session.scalar(
            select(
                func.extract(
                    "epoch", func.max(AuthThrottle.locked_until) - func.now()
                )
            ).where(
                AuthThrottle.locked_until > func.now(),
                or_(
                    *[
                        and_(
                            AuthThrottle.scope == scope,
                            AuthThrottle.key_hash == key_hash,
                        )
                        for scope, key_hash in keys
                    ]
                ),
            )
        )
        return 0 if remaining is None else max(1, math.ceil(remaining))

    async def reset(self, scope: str, key_hash: str) -> None:
        await self.session.execute(
            delete(AuthThrottle)
            .where(
                AuthThrottle.scope == scope, AuthThrottle.key_hash == key_hash
            )
            .execution_options(synchronize_session=False)
        )

    async def purge_stale(self, window: timedelta) -> int:
        """Delete rows whose lock and counting window have both ended."""
        result = await self.session.execute(
            delete(AuthThrottle)
            .where(
                (AuthThrottle.locked_until.is_(None))
                | (AuthThrottle.locked_until <= func.now()),
                AuthThrottle.window_started_at + window <= func.now(),
            )
            .returning(AuthThrottle.key_hash)
            .execution_options(synchronize_session=False)
        )
        return len(result.all())

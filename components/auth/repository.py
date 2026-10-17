import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, func, select, update

from components.auth.models import RefreshToken
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

    async def _revoke(self, condition: ColumnElement[bool]) -> int:
        result = await self.session.execute(
            update(RefreshToken)
            .where(condition, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.all())

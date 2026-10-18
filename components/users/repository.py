import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update

from components.core.enums import Role
from components.core.repository import BaseRepository
from components.users.models import User


class UserRepository(BaseRepository[User]):
    model = User

    async def create(
        self,
        email: str,
        password_hash: str,
        role: Role,
        organization_id: uuid.UUID | None,
        is_active: bool = True,
    ) -> User:
        return await self.add(
            User(
                email=email,
                email_lower=email.lower(),
                password_hash=password_hash,
                role=role.value,
                organization_id=organization_id,
                is_active=is_active,
            )
        )

    async def get_by_email(self, email: str) -> User | None:
        """Look up by email ignoring case."""
        return await self.session.scalar(
            select(User).where(User.email_lower == email.lower())
        )

    async def replace_password_hash(
        self, user_id: uuid.UUID, old_hash: str, new_hash: str
    ) -> bool:
        """Swap the hash only if it is still ``old_hash`` (rehash on login),
        so a concurrent password change is never overwritten."""
        result = await self.session.execute(
            update(User)
            .where(User.id == user_id, User.password_hash == old_hash)
            .values(password_hash=new_hash)
            .returning(User.id)
            .execution_options(synchronize_session=False)
        )
        return result.first() is not None

    async def list_page(
        self,
        limit: int,
        offset: int,
        organization_id: uuid.UUID | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
        user_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[User], int]:
        """Return one filtered page ordered by ``(created_at, id)`` and the
        total number of matching users."""
        conditions = []
        if user_id is not None:
            conditions.append(User.id == user_id)
        if organization_id is not None:
            conditions.append(User.organization_id == organization_id)
        if role is not None:
            conditions.append(User.role == role.value)
        if is_active is not None:
            conditions.append(User.is_active == is_active)
        total = await self.session.scalar(
            select(func.count()).select_from(User).where(*conditions)
        )
        result = await self.session.scalars(
            select(User)
            .where(*conditions)
            .order_by(User.created_at, User.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all(), total or 0

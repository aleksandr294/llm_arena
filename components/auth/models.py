import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from components.core.database import Base

# The foreign key below needs the users table in the metadata even when only
# this module is imported.
from components.users import models as _users_models  # noqa: F401


class RefreshToken(Base):
    """Server-side record of an issued refresh token. Only the SHA-256 of
    the token id is stored, never the token itself."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    # All tokens from one login share a family; reuse revokes the family.
    family_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

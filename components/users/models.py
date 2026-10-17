import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from components.core.database import Base

# Imported for its side effect: the foreign key below needs the organizations
# table in the metadata even when only this module is imported.
from components.organizations import models as _organizations  # noqa: F401


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Only superadmins are platform-level; everyone else has an
        # organization.
        CheckConstraint(
            "(role = 'superadmin') = (organization_id IS NULL)",
            name="ck_users_role_organization",
        ),
        Index("ix_users_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # RESTRICT: organizations are deactivated, never deleted with users.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), default=None
    )
    email: Mapped[str] = mapped_column(String(254))
    # Lowercased copy of ``email``; unique so emails collide ignoring case.
    email_lower: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    # Lowercase ``Role`` value; varchar, not a PG enum type.
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

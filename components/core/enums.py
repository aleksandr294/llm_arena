from enum import StrEnum


class Role(StrEnum):
    """User roles. Staff own their data, admins manage an organization and
    superadmins operate the platform (they belong to no organization)."""

    STAFF = "staff"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"

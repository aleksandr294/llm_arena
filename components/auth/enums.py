from enum import StrEnum


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class RejectionReason(StrEnum):
    EXPIRED = "expired"
    INVALID = "invalid"
    REVOKED = "revoked"

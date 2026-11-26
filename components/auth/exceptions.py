from components.auth.enums import RejectionReason


class InvalidCredentialsError(Exception):
    """Unknown email, wrong password, or inactive user or organization."""

    def __init__(self) -> None:
        super().__init__("invalid credentials")


class InvalidRefreshTokenError(Exception):
    """Refresh token is malformed, expired, revoked, used or unknown."""

    def __init__(self) -> None:
        super().__init__("invalid refresh token")


class AccessTokenRejectedError(Exception):
    def __init__(self, reason: RejectionReason) -> None:
        super().__init__(f"access token rejected: {reason.value}")
        self.reason = reason


class AuthThrottledError(Exception):
    """Too many failed attempts; ``retry_after`` is the seconds to wait."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("too many failed attempts")
        self.retry_after = retry_after


class PasswordPolicyError(ValueError):
    """The password violates the policy; the message never echoes it."""


class TokenError(Exception):
    """Base class for token validation failures (messages carry no token)."""


class TokenInvalidError(TokenError):
    pass


class TokenExpiredError(TokenError):
    pass

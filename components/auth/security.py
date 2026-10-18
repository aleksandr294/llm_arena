"""Password hashing and JWT helpers. No database or HTTP knowledge here."""

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from components.auth.enums import TokenType
from components.auth.exceptions import (
    ExpiredTokenError,
    InvalidTokenError,
    PasswordPolicyError,
)
from components.core.config import SettingsSchema

PASSWORD_MIN_LENGTH: Final = 12
PASSWORD_MAX_LENGTH: Final = 128

JWT_ALGORITHM: Final = "HS256"
_REQUIRED_CLAIMS: Final = ["exp", "iat", "iss", "sub", "jti", "typ"]

# Library defaults are the RFC 9106 recommended Argon2id parameters.
_hasher = PasswordHasher()
# Verified for unknown users so the response time matches a real check.
_DUMMY_HASH: Final = _hasher.hash("dummy-password-for-timing-equalisation")


def validate_password_policy(password: str) -> None:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(
            f"password must be {PASSWORD_MIN_LENGTH} to "
            f"{PASSWORD_MAX_LENGTH} characters"
        )
    if "\x00" in password:
        raise PasswordPolicyError("password must not contain NUL characters")


# Each Argon2id run uses ~64 MiB, so unbounded parallel logins could exhaust
# memory and the default thread pool. There is still no per-account or
# per-IP rate limiting (see the README).
HASH_CONCURRENCY_LIMIT: Final = 4
_hash_slots: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _hash_semaphore() -> asyncio.Semaphore:
    """The semaphore of the running loop, created lazily: a semaphore must
    not be shared between event loops (tests, reloads)."""
    global _hash_slots
    loop = asyncio.get_running_loop()
    if _hash_slots is None or _hash_slots[0] is not loop:
        _hash_slots = (loop, asyncio.Semaphore(HASH_CONCURRENCY_LIMIT))
    return _hash_slots[1]


async def _run_bounded[T](func: Callable[..., T], *args: object) -> T:
    async with _hash_semaphore():
        return await asyncio.to_thread(func, *args)


async def hash_password(password: str) -> str:
    """Hash with a random per-call salt, off the event loop."""
    return await _run_bounded(_hasher.hash, password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def verify_password(password_hash: str, password: str) -> bool:
    return await _run_bounded(_verify, password_hash, password)


async def verify_dummy_password(password: str) -> None:
    """Spend the cost of a verification for an unknown user; always fails."""
    await _run_bounded(_verify, _DUMMY_HASH, password)


def password_needs_rehash(password_hash: str) -> bool:
    """True when the stored hash uses outdated parameters."""
    return _hasher.check_needs_rehash(password_hash)


def hash_token_id(jti: str) -> str:
    """SHA-256 hex digest stored instead of the refresh token identifier."""
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    jti: str
    expires_at: datetime
    family_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    jti: str
    token_type: TokenType
    issued_at: datetime
    expires_at: datetime
    family_id: uuid.UUID | None


def _encode(
    settings: SettingsSchema,
    user_id: uuid.UUID,
    token_type: TokenType,
    ttl_seconds: int,
    family_id: uuid.UUID | None,
    now: datetime | None,
) -> IssuedToken:
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "iat": issued_at,
        "exp": expires_at,
        "jti": jti,
        "typ": token_type.value,
    }
    if family_id is not None:
        payload["fam"] = str(family_id)
    token = jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=JWT_ALGORITHM,
    )
    return IssuedToken(token, jti, expires_at, family_id)


def create_access_token(
    settings: SettingsSchema, user_id: uuid.UUID, now: datetime | None = None
) -> IssuedToken:
    return _encode(
        settings,
        user_id,
        TokenType.ACCESS,
        settings.jwt_access_ttl_seconds,
        None,
        now,
    )


def create_refresh_token(
    settings: SettingsSchema,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    now: datetime | None = None,
) -> IssuedToken:
    return _encode(
        settings,
        user_id,
        TokenType.REFRESH,
        settings.jwt_refresh_ttl_seconds,
        family_id,
        now,
    )


def _uuid_claim(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        raise InvalidTokenError("invalid token")
    try:
        return uuid.UUID(value)
    except ValueError:
        raise InvalidTokenError("invalid token") from None


def decode_token(
    settings: SettingsSchema,
    token: str,
    expected_type: TokenType,
    verify_expiry: bool = True,
) -> TokenClaims:
    """Verify signature, expiry, issuer, required claims and token type.

    ``verify_expiry=False`` is only for logout, which must still end the
    login of a correctly signed but expired token; everything else is
    verified as usual.

    The algorithm list is pinned, so ``alg: none`` and algorithm confusion
    are rejected. Raises ``ExpiredTokenError`` or ``InvalidTokenError``.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=settings.jwt_issuer,
            options={"require": _REQUIRED_CLAIMS, "verify_exp": verify_expiry},
        )
    except jwt.ExpiredSignatureError:
        raise ExpiredTokenError("token expired") from None
    except jwt.PyJWTError:
        raise InvalidTokenError("invalid token") from None

    if claims["typ"] != expected_type.value:
        raise InvalidTokenError("invalid token")
    jti = claims["jti"]
    if not isinstance(jti, str) or not jti:
        raise InvalidTokenError("invalid token")
    family_id = None
    if expected_type is TokenType.REFRESH:
        family_id = _uuid_claim(claims.get("fam"))
    return TokenClaims(
        user_id=_uuid_claim(claims["sub"]),
        jti=jti,
        token_type=expected_type,
        issued_at=datetime.fromtimestamp(claims["iat"], UTC),
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        family_id=family_id,
    )

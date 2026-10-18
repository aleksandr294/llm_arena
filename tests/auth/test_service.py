"""AuthService integration tests: need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select, update

from components.auth import security
from components.auth import service as service_module
from components.auth.models import RefreshToken
from components.auth.repository import RefreshTokenRepository
from components.auth.security import (
    create_access_token,
    create_refresh_token,
    hash_token_id,
)
from components.auth.service import (
    AuthService,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RejectionReason,
    TokenPair,
    ValidationResult,
)
from components.core.config import SettingsSchema
from components.core.enums import Role
from components.organizations.models import Organization
from components.users.models import User
from components.users.repository import UserRepository
from tests.auth.conftest import PASSWORD, Factory, seed_user

pytestmark = pytest.mark.integration


class Api:
    """Each call uses a fresh session, like one HTTP request."""

    def __init__(self, factory: Factory, settings: SettingsSchema) -> None:
        self.factory = factory
        self.settings = settings

    async def login(self, email: str, password: str = PASSWORD) -> TokenPair:
        async with self.factory() as s:
            return await AuthService(s, self.settings).login(email, password)

    async def refresh(self, token: str) -> TokenPair:
        async with self.factory() as s:
            return await AuthService(s, self.settings).refresh(token)

    async def logout(self, token: str) -> None:
        async with self.factory() as s:
            await AuthService(s, self.settings).logout(token)

    async def validate(self, token: str) -> ValidationResult:
        async with self.factory() as s:
            return await AuthService(s, self.settings).validate(token)

    async def revoke_all(self, user_id: uuid.UUID) -> None:
        async with self.factory() as s:
            await AuthService(s, self.settings).revoke_all_sessions(user_id)
            await s.commit()


@pytest.fixture
def api(session_factory: Factory, settings: SettingsSchema) -> Api:
    return Api(session_factory, settings)


async def _set_user(
    factory: Factory, user_id: uuid.UUID, **values: object
) -> None:
    async with factory() as s:
        await s.execute(update(User).where(User.id == user_id).values(**values))
        await s.commit()


# --- login -----------------------------------------------------------------


async def test_login_success(api: Api, session_factory: Factory) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    assert pair.access_token and pair.refresh_token
    assert pair.expires_in == api.settings.jwt_access_ttl_seconds


async def test_login_email_is_case_insensitive(
    api: Api, session_factory: Factory
) -> None:
    await seed_user(session_factory, email="Ann@Example.com")
    assert await api.login("ann@example.com")
    assert await api.login("  ANN@EXAMPLE.COM ")


async def test_login_failures_are_uniform(
    api: Api, session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_user(session_factory, "ok@example.com")
    await seed_user(session_factory, "off@example.com", is_active=False)
    await seed_user(session_factory, "org@example.com", org_active=False)
    dummy_calls: list[str] = []
    real = security.verify_dummy_password

    async def spy(password: str) -> None:
        dummy_calls.append("x")
        await real(password)

    monkeypatch.setattr(service_module, "verify_dummy_password", spy)

    attempts = [
        ("ok@example.com", "wrong password!!"),
        ("nobody@example.com", PASSWORD),
        ("off@example.com", PASSWORD),
        ("org@example.com", PASSWORD),
    ]
    errors = []
    for email, password in attempts:
        with pytest.raises(InvalidCredentialsError) as info:
            await api.login(email, password)
        errors.append((type(info.value), str(info.value), info.value.args))
    assert len(set(errors)) == 1
    assert len(dummy_calls) == 1  # only the unknown email


async def test_login_rehashes_outdated_hash(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    await _set_user(
        session_factory, user.user_id, password_hash=weak.hash(PASSWORD)
    )
    await api.login(user.email)
    async with session_factory() as s:
        stored = (await s.get(User, user.user_id)).password_hash  # type: ignore[union-attr]
    assert stored != weak.hash(PASSWORD)
    assert not PasswordHasher().check_needs_rehash(stored)
    assert await api.login(user.email)


async def test_login_rehash_does_not_overwrite_password_change(
    api: Api, session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await seed_user(session_factory)
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    old_hash = weak.hash(PASSWORD)
    await _set_user(session_factory, user.user_id, password_hash=old_hash)
    changed = PasswordHasher().hash("a brand new password")
    real_hash = security.hash_password

    async def hash_then_change(password: str) -> str:
        result = await real_hash(password)
        # The password is changed while the rehash is being computed.
        await _set_user(session_factory, user.user_id, password_hash=changed)
        return result

    monkeypatch.setattr(service_module, "hash_password", hash_then_change)
    await api.login(user.email)
    async with session_factory() as s:
        stored = (await s.get(User, user.user_id)).password_hash  # type: ignore[union-attr]
    assert stored == changed


async def test_replace_password_hash_is_conditional(
    session_factory: Factory,
) -> None:
    user = await seed_user(session_factory)
    async with session_factory() as s:
        repo = UserRepository(s)
        current = (await repo.get(user.user_id)).password_hash  # type: ignore[union-attr]
        assert not await repo.replace_password_hash(
            user.user_id, "not-the-current-hash", "new"
        )
        assert await repo.replace_password_hash(user.user_id, current, "new")
        await s.commit()
    async with session_factory() as s:
        assert (await s.get(User, user.user_id)).password_hash == "new"  # type: ignore[union-attr]


async def test_login_stores_only_hash_of_token_id(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    async with session_factory() as s:
        rows = (await s.scalars(select(RefreshToken))).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == user.user_id
    assert row.token_hash != pair.refresh_token
    assert pair.refresh_token not in row.token_hash
    assert len(row.token_hash) == 64
    assert row.used_at is None and row.revoked_at is None
    assert row.expires_at > datetime.now(UTC) + timedelta(days=6)


# --- refresh ---------------------------------------------------------------


async def test_refresh_rotates(api: Api, session_factory: Factory) -> None:
    user = await seed_user(session_factory)
    first = await api.login(user.email)
    second = await api.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    assert second.access_token
    third = await api.refresh(second.refresh_token)  # chain continues
    assert third.refresh_token
    async with session_factory() as s:
        rows = (await s.scalars(select(RefreshToken))).all()
    assert len({r.family_id for r in rows}) == 1
    assert sum(r.used_at is not None for r in rows) == 2


async def test_refresh_expired_token(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    past = datetime.now(UTC) - timedelta(days=30)
    issued = create_refresh_token(
        api.settings, user.user_id, uuid.uuid4(), now=past
    )
    async with session_factory() as s:
        await RefreshTokenRepository(s).create(
            user.user_id,
            issued.family_id or uuid.uuid4(),
            hash_token_id(issued.jti),
            issued.expires_at,
        )
        await s.commit()
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(issued.token)


async def test_refresh_expired_record_with_valid_jwt(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    async with session_factory() as s:
        await s.execute(
            update(RefreshToken).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await s.commit()
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(pair.refresh_token)


async def test_refresh_reuse_revokes_family(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    first = await api.login(user.email)
    second = await api.refresh(first.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(first.refresh_token)
    # The persisted revocation survives the failed call.
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(second.refresh_token)
    async with session_factory() as s:
        rows = (await s.scalars(select(RefreshToken))).all()
    assert all(r.revoked_at is not None for r in rows)


async def test_reuse_does_not_touch_other_logins(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    a = await api.login(user.email)
    b = await api.login(user.email)
    await api.refresh(a.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(a.refresh_token)
    assert await api.refresh(b.refresh_token)


async def test_concurrent_refresh_only_one_wins(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    results = await asyncio.gather(
        api.refresh(pair.refresh_token),
        api.refresh(pair.refresh_token),
        return_exceptions=True,
    )
    wins = [r for r in results if isinstance(r, TokenPair)]
    fails = [r for r in results if isinstance(r, InvalidRefreshTokenError)]
    assert len(wins) == 1 and len(fails) == 1
    # Losing the race is treated as reuse: the winner's token is revoked.
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(wins[0].refresh_token)


async def test_refresh_rejects_garbage_access_and_unknown(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh("not-a-token")
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(pair.access_token)
    unknown = create_refresh_token(api.settings, user.user_id, uuid.uuid4())
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(unknown.token)


async def test_refresh_fails_for_inactive_user_or_organization(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    await _set_user(session_factory, user.user_id, is_active=False)
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(pair.refresh_token)

    other = await seed_user(session_factory, "b@example.com")
    pair = await api.login(other.email)
    async with session_factory() as s:
        await s.execute(
            update(Organization)
            .where(Organization.id == other.organization_id)
            .values(is_active=False)
        )
        await s.commit()
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(pair.refresh_token)


async def test_revoke_all_sessions_ends_refresh(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    a = await api.login(user.email)
    b = await api.login(user.email)
    await api.revoke_all(user.user_id)
    for pair in (a, b):
        with pytest.raises(InvalidRefreshTokenError):
            await api.refresh(pair.refresh_token)


# --- logout ----------------------------------------------------------------


async def test_logout_revokes_family(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    first = await api.login(user.email)
    second = await api.refresh(first.refresh_token)
    await api.logout(second.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(second.refresh_token)


async def test_logout_is_idempotent(api: Api, session_factory: Factory) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    await api.logout(pair.refresh_token)
    await api.logout(pair.refresh_token)
    unknown = create_refresh_token(api.settings, user.user_id, uuid.uuid4())
    await api.logout(unknown.token)


async def test_logout_rejects_unusable_token(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    with pytest.raises(InvalidRefreshTokenError):
        await api.logout("garbage")
    with pytest.raises(InvalidRefreshTokenError):
        await api.logout(pair.access_token)
    other = create_refresh_token(
        api.settings.model_copy(update={"jwt_issuer": "other"}),
        user.user_id,
        uuid.uuid4(),
    )
    with pytest.raises(InvalidRefreshTokenError):
        await api.logout(other.token)


async def test_logout_with_expired_token_revokes_family(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    # Make the stored login look expired and sign a matching expired token.
    async with session_factory() as s:
        family_id = (await s.scalars(select(RefreshToken))).one().family_id
    expired = create_refresh_token(
        api.settings,
        user.user_id,
        family_id,
        now=datetime.now(UTC) - timedelta(days=30),
    )
    async with session_factory() as s:
        await s.execute(
            update(RefreshToken).values(
                token_hash=hash_token_id(expired.jti),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await s.commit()

    await api.logout(expired.token)  # idempotent success, no error
    await api.logout(expired.token)
    async with session_factory() as s:
        row = (await s.scalars(select(RefreshToken))).one()
    assert row.revoked_at is not None
    with pytest.raises(InvalidRefreshTokenError):
        await api.refresh(pair.refresh_token)


# --- validate / authenticate ----------------------------------------------


async def test_validate_valid_token(api: Api, session_factory: Factory) -> None:
    user = await seed_user(session_factory, role=Role.ADMIN)
    pair = await api.login(user.email)
    result = await api.validate(pair.access_token)
    assert result.valid
    assert result.user_id == user.user_id
    assert result.organization_id == user.organization_id
    assert result.role is Role.ADMIN
    assert result.expires_at is not None and result.reason is None


async def test_validate_superadmin_has_no_organization(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory, role=Role.SUPERADMIN)
    result = await api.validate((await api.login(user.email)).access_token)
    assert result.valid and result.organization_id is None
    assert result.role is Role.SUPERADMIN


async def test_validate_expired_token(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    old = create_access_token(
        api.settings, user.user_id, now=datetime.now(UTC) - timedelta(days=1)
    )
    result = await api.validate(old.token)
    assert not result.valid and result.reason is RejectionReason.EXPIRED


async def test_validate_invalid_tokens(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await api.login(user.email)
    for token in ("garbage", pair.refresh_token, pair.access_token + "x"):
        result = await api.validate(token)
        assert not result.valid and result.reason is RejectionReason.INVALID
    ghost = create_access_token(api.settings, uuid.uuid4())
    result = await api.validate(ghost.token)
    assert not result.valid and result.reason is RejectionReason.INVALID


async def test_validate_deactivated_user_or_org(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    token = (await api.login(user.email)).access_token
    assert (await api.validate(token)).valid
    await _set_user(session_factory, user.user_id, is_active=False)
    result = await api.validate(token)
    assert not result.valid and result.reason is RejectionReason.REVOKED

    other = await seed_user(session_factory, "b@example.com")
    token = (await api.login(other.email)).access_token
    async with session_factory() as s:
        await s.execute(
            update(Organization)
            .where(Organization.id == other.organization_id)
            .values(is_active=False)
        )
        await s.commit()
    result = await api.validate(token)
    assert not result.valid and result.reason is RejectionReason.REVOKED


async def test_validate_uses_database_role(
    api: Api, session_factory: Factory
) -> None:
    user = await seed_user(session_factory, role=Role.STAFF)
    token = (await api.login(user.email)).access_token
    await _set_user(session_factory, user.user_id, role="admin")
    assert (await api.validate(token)).role is Role.ADMIN

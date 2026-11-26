"""Access tokens end with the session, and user rows are locked against
races. Need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from components.auth.dependencies import build_auth_service
from components.auth.exceptions import InvalidRefreshTokenError
from components.auth.models import RefreshToken
from components.auth.security import create_access_token
from components.core.config import SettingsSchema
from components.core.database import session_scope
from components.core.enums import Role
from components.core.schemas import Principal
from components.users.dependencies import build_user_service
from components.users.models import User
from components.users.repository import UserRepository
from tests.auth.helpers import PASSWORD, Factory, Seeded, seed_user

API = "/api/v1"
NEW_PASSWORD = "another long password"


@pytest.fixture(autouse=True)
def _api(api: None) -> None:
    return None


def _earlier_token(settings: SettingsSchema, user: Seeded) -> str:
    """A valid access token issued before now (the revocation instant is
    the current second, and a token of that same second survives)."""
    issued = datetime.now(UTC) - timedelta(seconds=30)
    return create_access_token(settings, user.user_id, now=issued).token


async def _status(client: AsyncClient, token: str) -> int:
    response = await client.get(
        f"{API}/users", headers={"Authorization": f"Bearer {token}"}
    )
    return response.status_code


async def _validate(client: AsyncClient, token: str) -> dict[str, object]:
    response = await client.post(f"{API}/auth/validate", json={"token": token})
    assert response.status_code == 200
    return dict(response.json())


async def _login(client: AsyncClient, user: Seeded) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/login", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return dict(response.json())


async def test_logout_ends_earlier_access_tokens(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    earlier = _earlier_token(settings, user)
    login = await _login(client, user)
    assert await _status(client, earlier) == 200

    response = await client.post(
        f"{API}/auth/logout", json={"refresh_token": login["refresh_token"]}
    )

    assert response.status_code == 204
    assert await _status(client, earlier) == 401
    body = await _validate(client, earlier)
    assert body["valid"] is False and body["reason"] == "revoked"


async def test_new_login_after_logout_is_accepted(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    first = await _login(client, user)
    await client.post(
        f"{API}/auth/logout", json={"refresh_token": first["refresh_token"]}
    )

    second = await _login(client, user)

    assert await _status(client, second["access_token"]) == 200


async def test_password_change_ends_access_tokens_and_login_works(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    admin = await seed_user(session_factory, "boss@example.com", Role.ADMIN)
    staff = await seed_user(
        session_factory,
        "worker@example.com",
        Role.STAFF,
        existing_organization_id=admin.organization_id,
    )
    earlier = _earlier_token(settings, staff)
    assert await _status(client, earlier) == 200
    admin_login = await _login(client, admin)

    response = await client.patch(
        f"{API}/users/{staff.user_id}",
        json={"password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {admin_login['access_token']}"},
    )

    assert response.status_code == 200
    assert await _status(client, earlier) == 401
    relogin = await client.post(
        f"{API}/auth/login",
        json={"email": staff.email, "password": NEW_PASSWORD},
    )
    assert relogin.status_code == 200
    assert await _status(client, relogin.json()["access_token"]) == 200


async def test_deactivation_ends_access_tokens_even_after_reactivation(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    admin = await seed_user(session_factory, "boss@example.com", Role.ADMIN)
    staff = await seed_user(
        session_factory,
        "worker@example.com",
        Role.STAFF,
        existing_organization_id=admin.organization_id,
    )
    earlier = _earlier_token(settings, staff)
    token = (await _login(client, admin))["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for active in (False, True):
        response = await client.patch(
            f"{API}/users/{staff.user_id}",
            json={"is_active": active},
            headers=headers,
        )
        assert response.status_code == 200

    # Active again, but a token issued before the deactivation stays dead.
    assert await _status(client, earlier) == 401
    async with session_factory() as session:
        marker = await session.scalar(
            select(User.tokens_valid_after).where(User.id == staff.user_id)
        )
    assert marker is not None


async def test_token_issued_in_the_revocation_second_survives(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    now = datetime.now(UTC).replace(microsecond=0)
    token = create_access_token(settings, user.user_id, now=now).token
    async with session_scope(session_factory) as session:
        await session.execute(
            update(User)
            .where(User.id == user.user_id)
            .values(tokens_valid_after=now + timedelta(milliseconds=900))
        )
        service = build_auth_service(session, settings)
        assert (await service.validate(token)).valid is True


# --- row locking ---------------------------------------------------------


async def test_deactivation_wins_over_an_inflight_refresh(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    async with session_scope(session_factory) as session:
        pair = await build_auth_service(session, settings).login(
            user.email, PASSWORD
        )

    async def refresh() -> None:
        async with session_scope(session_factory) as session:
            await build_auth_service(session, settings).refresh(
                pair.refresh_token
            )

    async with session_scope(session_factory) as session:
        # The deactivation holds the user row, as ``update_user`` does.
        locked = await UserRepository(session).get_for_update(user.user_id)
        assert locked is not None
        locked.is_active = False
        await build_auth_service(session, settings).revoke_all_sessions(
            user.user_id
        )
        refreshing = asyncio.create_task(refresh())
        await asyncio.sleep(0.3)
        assert not refreshing.done()  # waits for the lock

    with pytest.raises(InvalidRefreshTokenError):
        await refreshing
    async with session_factory() as session:
        live = (
            await session.scalars(
                select(RefreshToken).where(RefreshToken.revoked_at.is_(None))
            )
        ).all()
    assert live == []


async def test_parallel_role_and_password_updates_both_apply(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    admin = await seed_user(session_factory, "boss@example.com", Role.ADMIN)
    staff = await seed_user(
        session_factory,
        "worker@example.com",
        Role.STAFF,
        existing_organization_id=admin.organization_id,
    )
    caller = Principal(admin.user_id, admin.organization_id, Role.ADMIN)

    async def update(**changes: object) -> None:
        async with session_scope(session_factory) as session:
            auth = build_auth_service(session, settings)
            await build_user_service(session, auth).update_user(
                caller,
                staff.user_id,
                **changes,  # type: ignore[arg-type]
            )

    await asyncio.gather(update(role=Role.ADMIN), update(password=NEW_PASSWORD))

    async with session_scope(session_factory) as session:
        pair = await build_auth_service(session, settings).login(
            staff.email, NEW_PASSWORD
        )
        user = await session.get(User, staff.user_id)
    assert pair.access_token
    assert user is not None and user.role == Role.ADMIN.value


async def test_unknown_user_cannot_be_locked_for_update(
    session_factory: Factory,
) -> None:
    async with session_scope(session_factory) as session:
        assert (
            await UserRepository(session).get_for_update(uuid.uuid4()) is None
        )


async def test_concurrent_logout_and_refresh_of_one_family_do_not_deadlock(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    for _ in range(5):
        async with session_scope(session_factory) as session:
            pair = await build_auth_service(session, settings).login(
                user.email, PASSWORD
            )

        async def logout(token: str = pair.refresh_token) -> None:
            async with session_scope(session_factory) as session:
                await build_auth_service(session, settings).logout(token)

        async def refresh(token: str = pair.refresh_token) -> None:
            async with session_scope(session_factory) as session:
                await build_auth_service(session, settings).refresh(token)

        results = await asyncio.wait_for(
            asyncio.gather(logout(), refresh(), return_exceptions=True), 30
        )

        for result in results:
            assert result is None or isinstance(
                result, InvalidRefreshTokenError
            ), result


async def test_organization_deactivation_keeps_old_access_tokens_dead(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    root = await seed_user(session_factory, "root@example.com", Role.SUPERADMIN)
    staff = await seed_user(session_factory, "worker@example.com")
    earlier = _earlier_token(settings, staff)
    assert await _status(client, earlier) == 200
    token = (await _login(client, root))["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for active in (False, True):
        response = await client.patch(
            f"{API}/organizations/{staff.organization_id}",
            json={"is_active": active},
            headers=headers,
        )
        assert response.status_code == 200

    assert await _status(client, earlier) == 401

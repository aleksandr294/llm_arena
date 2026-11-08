"""Sign-in throttling. Need PostgreSQL (``DATABASE_URL``)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from api.v1.utils import get_settings_dependency
from components.auth import security
from components.auth import service as service_module
from components.auth.dependencies import (
    build_auth_service,
    build_auth_throttle_service,
)
from components.auth.exceptions import (
    AuthThrottledError,
    InvalidCredentialsError,
)
from components.auth.models import AuthThrottle
from components.auth.security import (
    create_access_token,
    create_refresh_token,
)
from components.auth.throttle import account_key, address_key
from components.core.config import SettingsSchema
from components.core.database import session_scope
from tests.auth.helpers import PASSWORD, Factory, Seeded, seed_user

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
VALIDATE = "/api/v1/auth/validate"
LOCKOUT = 600
MAX_FAILURES = 3


@pytest.fixture
def throttled_settings(settings: SettingsSchema) -> SettingsSchema:
    return settings.model_copy(
        update={
            "auth_max_failed_attempts": MAX_FAILURES,
            "auth_max_failed_attempts_per_address": MAX_FAILURES,
            "auth_lockout_seconds": LOCKOUT,
        }
    )


@pytest.fixture(autouse=True)
def _api(api: None, app: FastAPI, throttled_settings: SettingsSchema) -> None:
    app.dependency_overrides[get_settings_dependency] = (
        lambda: throttled_settings
    )


async def _fail(client: AsyncClient, email: str, times: int) -> None:
    for _ in range(times):
        response = await client.post(
            LOGIN, json={"email": email, "password": "wrong password!!"}
        )
        assert response.status_code == 401


async def _login(
    client: AsyncClient, email: str, password: str = PASSWORD
) -> int:
    response = await client.post(
        LOGIN, json={"email": email, "password": password}
    )
    return response.status_code


async def test_failures_below_the_limit_do_not_lock(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)

    await _fail(client, user.email, MAX_FAILURES - 1)

    assert await _login(client, user.email) == 200


async def test_lockout_rejects_the_correct_password_with_retry_after(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    await _fail(client, user.email, MAX_FAILURES)

    response = await client.post(
        LOGIN, json={"email": user.email, "password": PASSWORD}
    )

    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert LOCKOUT - 30 <= retry_after <= LOCKOUT
    assert PASSWORD not in response.text


async def test_unknown_account_is_locked_like_a_known_one(
    client: AsyncClient,
) -> None:
    await _fail(client, "nobody@example.com", MAX_FAILURES)

    response = await client.post(
        LOGIN, json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert response.status_code == 429
    assert "Retry-After" in response.headers


async def test_email_case_and_spaces_share_one_counter(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory, "Mixed@Example.com")
    variants = ["mixed@example.com", "MIXED@EXAMPLE.COM", " Mixed@Example.com "]
    for email in variants:
        await _fail(client, email, 1)

    assert await _login(client, "mixed@example.com") == 429
    assert user.email == "Mixed@Example.com"


async def test_failure_is_persisted_although_the_request_fails(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)

    await _fail(client, user.email, 1)

    async with session_factory() as session:
        rows = (await session.scalars(select(AuthThrottle))).all()
    assert sorted(r.scope for r in rows) == ["account", "address"]
    assert all(r.failures == 1 and r.locked_until is None for r in rows)
    # Only hashes are stored.
    assert all(len(r.key_hash) == 64 for r in rows)
    assert user.email.lower() not in " ".join(r.key_hash for r in rows)


async def test_address_lock_covers_other_accounts_but_not_other_addresses(
    app: FastAPI, client: AsyncClient, session_factory: Factory
) -> None:
    other = await seed_user(session_factory, "other@example.com")
    for index in range(MAX_FAILURES):
        await _fail(client, f"nobody{index}@example.com", 1)

    assert await _login(client, other.email) == 429

    transport = ASGITransport(app=app, client=("10.9.9.9", 1234))
    async with AsyncClient(transport=transport, base_url="http://t") as far:
        assert await _login(far, other.email) == 200


async def test_lock_ends_and_a_correct_login_resets_the_counter(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    await _fail(client, user.email, MAX_FAILURES)
    long_ago = datetime.now(UTC) - timedelta(seconds=LOCKOUT + 5)
    async with session_scope(session_factory) as session:
        await session.execute(
            update(AuthThrottle).values(
                window_started_at=long_ago, locked_until=long_ago
            )
        )

    assert await _login(client, user.email) == 200

    async with session_factory() as session:
        scopes = (await session.scalars(select(AuthThrottle.scope))).all()
    assert "account" not in scopes  # reset on success
    assert scopes == ["address"]  # the address counter keeps counting


async def test_failure_after_the_window_restarts_the_count(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    await _fail(client, user.email, MAX_FAILURES - 1)
    long_ago = datetime.now(UTC) - timedelta(seconds=LOCKOUT + 5)
    async with session_scope(session_factory) as session:
        await session.execute(
            update(AuthThrottle).values(window_started_at=long_ago)
        )

    await _fail(client, user.email, 1)

    async with session_factory() as session:
        counts = (await session.scalars(select(AuthThrottle.failures))).all()
    assert counts == [1, 1]
    assert await _login(client, user.email) == 200


async def test_concurrent_failures_are_all_counted(
    session_factory: Factory, throttled_settings: SettingsSchema
) -> None:
    keys = [account_key("race@example.com"), address_key("10.0.0.7")]
    settings = throttled_settings.model_copy(
        update={
            "auth_max_failed_attempts": 1000,
            "auth_max_failed_attempts_per_address": 1000,
        }
    )

    async def fail() -> None:
        async with session_scope(session_factory) as session:
            await build_auth_throttle_service(session, settings).record_failure(
                keys
            )

    await asyncio.gather(*[fail() for _ in range(12)])

    async with session_factory() as session:
        counts = (await session.scalars(select(AuthThrottle.failures))).all()
    assert counts == [12, 12]


async def test_refresh_with_bad_tokens_locks_the_address(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    login = await client.post(
        LOGIN, json={"email": user.email, "password": PASSWORD}
    )
    for _ in range(MAX_FAILURES):
        bad = await client.post(REFRESH, json={"refresh_token": "junk"})
        assert bad.status_code == 401

    good = await client.post(
        REFRESH, json={"refresh_token": login.json()["refresh_token"]}
    )

    assert good.status_code == 429
    assert "Retry-After" in good.headers


async def test_validate_with_bad_tokens_locks_the_address(
    client: AsyncClient,
) -> None:
    for _ in range(MAX_FAILURES):
        response = await client.post(VALIDATE, json={"token": "junk"})
        assert response.status_code == 200
        assert response.json()["valid"] is False

    response = await client.post(VALIDATE, json={"token": "junk"})

    assert response.status_code == 429
    assert "Retry-After" in response.headers


async def test_valid_tokens_are_not_counted_by_validate(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    token = (
        await client.post(
            LOGIN, json={"email": user.email, "password": PASSWORD}
        )
    ).json()["access_token"]

    for _ in range(MAX_FAILURES + 2):
        response = await client.post(VALIDATE, json={"token": token})
        assert response.json()["valid"] is True


async def test_forged_refresh_token_for_a_user_is_counted(
    client: AsyncClient,
    session_factory: Factory,
    throttled_settings: SettingsSchema,
) -> None:
    user: Seeded = await seed_user(session_factory)
    unknown_record = create_refresh_token(
        throttled_settings, user.user_id, user.user_id
    ).token
    for _ in range(MAX_FAILURES):
        response = await client.post(
            REFRESH, json={"refresh_token": unknown_record}
        )
        assert response.status_code == 401

    response = await client.post(
        REFRESH, json={"refresh_token": unknown_record}
    )

    assert response.status_code == 429


async def test_throttle_service_purges_stale_rows(
    session_factory: Factory, throttled_settings: SettingsSchema
) -> None:
    async with session_scope(session_factory) as session:
        service = build_auth_throttle_service(session, throttled_settings)
        await service.record_failure([account_key("a@example.com")])
    long_ago = datetime.now(UTC) - timedelta(seconds=LOCKOUT + 5)
    async with session_scope(session_factory) as session:
        await session.execute(
            update(AuthThrottle).values(window_started_at=long_ago)
        )
    async with session_scope(session_factory) as session:
        service = build_auth_throttle_service(session, throttled_settings)
        assert await service.purge_stale() == 1


async def test_address_counter_has_its_own_higher_threshold(
    app: FastAPI,
    client: AsyncClient,
    session_factory: Factory,
    settings: SettingsSchema,
) -> None:
    roomy = settings.model_copy(
        update={
            "auth_max_failed_attempts": MAX_FAILURES,
            "auth_max_failed_attempts_per_address": 50,
        }
    )
    app.dependency_overrides[get_settings_dependency] = lambda: roomy
    other = await seed_user(session_factory, "other@example.com")
    for index in range(MAX_FAILURES + 2):
        await _fail(client, f"nobody{index}@example.com", 1)

    # Five failures from one address, each for another account: no lock.
    assert await _login(client, other.email) == 200


async def test_expired_tokens_are_not_counted_by_validate(
    client: AsyncClient,
    session_factory: Factory,
    throttled_settings: SettingsSchema,
) -> None:
    user = await seed_user(session_factory)
    stale = create_access_token(
        throttled_settings,
        user.user_id,
        now=datetime.now(UTC) - timedelta(days=1),
    ).token
    for _ in range(MAX_FAILURES + 2):
        response = await client.post(VALIDATE, json={"token": stale})
        assert response.json()["reason"] == "expired"

    ok = await client.post(VALIDATE, json={"token": "junk"})
    assert ok.status_code == 200  # the address was never locked


async def test_parallel_wrong_guesses_verify_at_most_the_threshold(
    session_factory: Factory,
    throttled_settings: SettingsSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await seed_user(session_factory)
    verifications = 0
    real = security.verify_password

    async def counting(password_hash: str, password: str) -> bool:
        nonlocal verifications
        verifications += 1
        await asyncio.sleep(0.2)  # the slow hash: the whole burst overlaps
        return await real(password_hash, password)

    monkeypatch.setattr(service_module, "verify_password", counting)

    async def guess() -> BaseException | None:
        async with session_scope(session_factory) as session:
            try:
                await build_auth_service(session, throttled_settings).login(
                    user.email, "wrong password!!"
                )
            except (InvalidCredentialsError, AuthThrottledError) as exc:
                return exc
        return None

    outcomes = await asyncio.gather(*[guess() for _ in range(10)])

    assert verifications == MAX_FAILURES
    assert (
        sum(isinstance(o, InvalidCredentialsError) for o in outcomes)
        == MAX_FAILURES
    )
    assert sum(isinstance(o, AuthThrottledError) for o in outcomes) == 7


async def test_unknown_account_burst_is_bounded_the_same_way(
    session_factory: Factory, throttled_settings: SettingsSchema
) -> None:
    async def guess() -> BaseException | None:
        async with session_scope(session_factory) as session:
            try:
                await build_auth_service(session, throttled_settings).login(
                    "ghost@example.com", "wrong password!!"
                )
            except (InvalidCredentialsError, AuthThrottledError) as exc:
                return exc
        return None

    outcomes = await asyncio.gather(*[guess() for _ in range(8)])

    assert (
        sum(isinstance(o, InvalidCredentialsError) for o in outcomes)
        == MAX_FAILURES
    )
    assert sum(isinstance(o, AuthThrottledError) for o in outcomes) == 5


async def test_correct_login_after_failures_succeeds_and_resets(
    session_factory: Factory, throttled_settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    for _ in range(MAX_FAILURES - 1):
        async with session_scope(session_factory) as session:
            with pytest.raises(InvalidCredentialsError):
                await build_auth_service(session, throttled_settings).login(
                    user.email, "wrong password!!"
                )
    async with session_scope(session_factory) as session:
        pair = await build_auth_service(session, throttled_settings).login(
            user.email, PASSWORD
        )
    assert pair.access_token
    async with session_factory() as session:
        rows = (await session.scalars(select(AuthThrottle))).all()
    assert rows == []  # no address given: the account counter is gone

import asyncio
import base64
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from argon2 import PasswordHasher

from components.auth import security
from components.auth.enums import TokenType
from components.auth.exceptions import (
    PasswordPolicyError,
    TokenExpiredError,
    TokenInvalidError,
)
from components.auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token_id,
    password_needs_rehash,
    validate_password_policy,
    verify_dummy_password,
    verify_password,
)
from components.core.config import SettingsSchema
from tests.helpers import make_settings

SECRET = "unit-test-signing-key-0123456789abcdefghij"
PASSWORD = "correct horse battery"


def _settings(**overrides: Any) -> SettingsSchema:
    return make_settings(**{"jwt_secret_key": SECRET, **overrides})


# --- passwords -------------------------------------------------------------


async def test_hash_is_argon2id_salted_and_hides_password() -> None:
    first = await hash_password(PASSWORD)
    second = await hash_password(PASSWORD)

    assert first.startswith("$argon2id$")
    assert PASSWORD not in first
    assert first != second


async def test_verify_correct_and_wrong_password() -> None:
    stored = await hash_password(PASSWORD)

    assert await verify_password(stored, PASSWORD) is True
    assert await verify_password(stored, PASSWORD + "x") is False


async def test_verify_malformed_hash_is_false_not_error() -> None:
    assert await verify_password("not-a-hash", PASSWORD) is False


async def test_dummy_verification_runs_one_verification_against_the_dummy_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    real_verify = security._verify

    def spy(password_hash: str, password: str) -> bool:
        calls.append((password_hash, password))
        return real_verify(password_hash, password)

    monkeypatch.setattr(security, "_verify", spy)

    await verify_dummy_password(PASSWORD)
    # One verification of the password against the fixed dummy hash, which
    # is a valid hash (so the full Argon2 cost is paid) that never matches.
    assert calls == [(security._dummy_hash(), PASSWORD)]
    assert await verify_password(security._dummy_hash(), PASSWORD) is False


async def test_current_hash_does_not_need_rehash() -> None:
    assert password_needs_rehash(await hash_password(PASSWORD)) is False


def test_outdated_hash_needs_rehash() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

    assert password_needs_rehash(weak.hash(PASSWORD)) is True


@pytest.mark.parametrize("length", [12, 128])
def test_policy_accepts_boundaries(length: int) -> None:
    # Raises PasswordPolicyError when rejected; the neighbours just outside
    # the boundary are rejected below.
    validate_password_policy("a" * length)


@pytest.mark.parametrize("length", [11, 129])
def test_policy_rejects_just_outside_the_boundaries(length: int) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("a" * length)


@pytest.mark.parametrize(
    "password", ["", "a" * 11, "a" * 129, "valid-password\x00x"]
)
def test_policy_rejects(password: str) -> None:
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy(password)

    assert "valid-password" not in str(exc.value)


# --- tokens ----------------------------------------------------------------


def test_access_token_roundtrip_and_default_lifetime() -> None:
    settings = _settings()
    user_id = uuid.uuid4()

    issued = create_access_token(settings, user_id)
    claims = decode_token(settings, issued.token, TokenType.ACCESS)

    assert claims.user_id == user_id
    assert claims.jti == issued.jti
    assert claims.family_id is None
    assert claims.expires_at - claims.issued_at == timedelta(minutes=15)


def test_refresh_token_carries_family_and_week_lifetime() -> None:
    settings = _settings()
    family = uuid.uuid4()

    issued = create_refresh_token(settings, uuid.uuid4(), family)
    claims = decode_token(settings, issued.token, TokenType.REFRESH)

    assert claims.family_id == family
    assert claims.expires_at - claims.issued_at == timedelta(days=7)


def test_jti_is_unique() -> None:
    settings = _settings()
    user_id = uuid.uuid4()

    assert (
        create_access_token(settings, user_id).jti
        != create_access_token(settings, user_id).jti
    )


def test_expired_token_is_rejected() -> None:
    settings = _settings()
    past = datetime.now(UTC) - timedelta(hours=2)
    issued = create_access_token(settings, uuid.uuid4(), now=past)

    with pytest.raises(TokenExpiredError):
        decode_token(settings, issued.token, TokenType.ACCESS)


def test_wrong_issuer_is_rejected() -> None:
    issued = create_access_token(_settings(jwt_issuer="other"), uuid.uuid4())

    with pytest.raises(TokenInvalidError):
        decode_token(_settings(), issued.token, TokenType.ACCESS)


def test_wrong_signing_key_is_rejected() -> None:
    other = _settings(jwt_secret_key="another-signing-key-0123456789abcdefgh")
    issued = create_access_token(other, uuid.uuid4())

    with pytest.raises(TokenInvalidError):
        decode_token(_settings(), issued.token, TokenType.ACCESS)


def test_tampered_payload_is_rejected() -> None:
    settings = _settings()
    header, payload, signature = create_access_token(
        settings, uuid.uuid4()
    ).token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["sub"] = str(uuid.uuid4())
    forged = (
        base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    )

    with pytest.raises(TokenInvalidError):
        decode_token(
            settings, f"{header}.{forged}.{signature}", TokenType.ACCESS
        )


def test_algorithm_none_is_rejected() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    unsigned = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": "x",
            "typ": "access",
        },
        "",  # the "none" algorithm takes no key
        algorithm="none",
    )

    with pytest.raises(TokenInvalidError):
        decode_token(settings, unsigned, TokenType.ACCESS)


def test_other_hmac_algorithm_is_rejected() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": "x",
            "typ": "access",
        },
        SECRET * 2,
        algorithm="HS512",
    )

    with pytest.raises(TokenInvalidError):
        decode_token(settings, token, TokenType.ACCESS)


def test_wrong_token_type_is_rejected_both_ways() -> None:
    settings = _settings()
    user_id = uuid.uuid4()
    access = create_access_token(settings, user_id).token
    refresh = create_refresh_token(settings, user_id, uuid.uuid4()).token

    with pytest.raises(TokenInvalidError):
        decode_token(settings, refresh, TokenType.ACCESS)
    with pytest.raises(TokenInvalidError):
        decode_token(settings, access, TokenType.REFRESH)


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "sub", "jti", "typ"])
def test_missing_required_claim_is_rejected(missing: str) -> None:
    settings = _settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": "x",
        "typ": "access",
    }
    del payload[missing]
    token = jwt.encode(payload, SECRET, algorithm="HS256")

    with pytest.raises(TokenInvalidError):
        decode_token(settings, token, TokenType.ACCESS)


def test_refresh_without_family_or_bad_subject_is_rejected() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": "x",
        "typ": "refresh",
    }
    no_family = jwt.encode(base, SECRET, algorithm="HS256")
    bad_sub = jwt.encode(
        {**base, "fam": str(uuid.uuid4()), "sub": "nope"},
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(TokenInvalidError):
        decode_token(settings, no_family, TokenType.REFRESH)
    with pytest.raises(TokenInvalidError):
        decode_token(settings, bad_sub, TokenType.REFRESH)


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_garbage_is_rejected(token: str) -> None:
    with pytest.raises(TokenInvalidError):
        decode_token(_settings(), token, TokenType.ACCESS)


def test_errors_do_not_leak_token_or_secret() -> None:
    settings = _settings()
    token = create_access_token(settings, uuid.uuid4()).token

    with pytest.raises(TokenInvalidError) as exc:
        decode_token(_settings(jwt_issuer="other"), token, TokenType.ACCESS)

    assert token not in str(exc.value)
    assert SECRET not in str(exc.value)


def test_token_id_hash_is_sha256_hex() -> None:
    digest = hash_token_id("abc")

    assert len(digest) == 64
    assert digest == hash_token_id("abc")
    assert digest != hash_token_id("abd")


def test_decode_without_expiry_check_accepts_expired_refresh_token() -> None:
    settings = _settings()
    past = datetime.now(UTC) - timedelta(days=90)
    issued = create_refresh_token(
        settings, uuid.uuid4(), uuid.uuid4(), now=past
    )

    with pytest.raises(TokenExpiredError):
        decode_token(settings, issued.token, TokenType.REFRESH)
    claims = decode_token(
        settings, issued.token, TokenType.REFRESH, verify_expiry=False
    )
    assert claims.jti == issued.jti


def test_decode_without_expiry_check_still_verifies_the_rest() -> None:
    settings = _settings()
    past = datetime.now(UTC) - timedelta(days=90)
    user_id, family = uuid.uuid4(), uuid.uuid4()
    forged = jwt.encode(
        {
            "sub": str(user_id),
            "iss": settings.jwt_issuer,
            "iat": past,
            "exp": past + timedelta(days=7),
            "jti": "x",
            "typ": "refresh",
            "fam": str(family),
        },
        "another-signing-key-0123456789abcdefghijkl",
        algorithm="HS256",
    )
    other_issuer = create_refresh_token(
        _settings(jwt_issuer="other"), user_id, family, now=past
    )
    wrong_type = create_access_token(settings, user_id, now=past)
    for token in (forged, other_issuer.token, wrong_type.token, "garbage"):
        with pytest.raises(TokenInvalidError):
            decode_token(
                settings, token, TokenType.REFRESH, verify_expiry=False
            )


async def _max_concurrency(monkeypatch: pytest.MonkeyPatch) -> int:
    lock = threading.Lock()
    state = {"running": 0, "max": 0}
    # Set once as many verifications as the limit allows run at the same
    # time; the first ones wait for it, so they overlap without a sleep.
    saturated = threading.Event()

    def slow_verify(password_hash: str, password: str) -> bool:
        with lock:
            state["running"] += 1
            state["max"] = max(state["max"], state["running"])
            if state["running"] >= security.HASH_CONCURRENCY_LIMIT:
                saturated.set()
        # Bounded: a limit that never saturates fails the test, not hangs it.
        saturated.wait(timeout=10)
        with lock:
            state["running"] -= 1
        return True

    monkeypatch.setattr(security, "_verify", slow_verify)
    await asyncio.gather(
        *(security.verify_password("h", "p") for _ in range(16))
    )
    return state["max"]


async def test_password_hashing_concurrency_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peak = await _max_concurrency(monkeypatch)
    assert peak == security.HASH_CONCURRENCY_LIMIT


def test_semaphore_works_under_several_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for _ in range(2):
        peak = asyncio.run(_max_concurrency(monkeypatch))
        assert peak == security.HASH_CONCURRENCY_LIMIT


# --- clock-skew leeway ---------------------------------------------------


def _expired_ago(seconds: int, settings: SettingsSchema) -> str:
    """An access token whose ``exp`` was ``seconds`` ago."""
    issued = datetime.now(UTC) - timedelta(
        seconds=settings.jwt_access_ttl_seconds + seconds
    )
    return create_access_token(settings, uuid.uuid4(), now=issued).token


def test_token_expired_within_leeway_is_accepted() -> None:
    settings = _settings(jwt_leeway_seconds=5)

    claims = decode_token(settings, _expired_ago(2, settings), TokenType.ACCESS)

    assert claims.token_type is TokenType.ACCESS


def test_token_expired_beyond_leeway_is_rejected() -> None:
    settings = _settings(jwt_leeway_seconds=5)

    with pytest.raises(TokenExpiredError):
        decode_token(settings, _expired_ago(10, settings), TokenType.ACCESS)


def test_zero_leeway_is_strict() -> None:
    settings = _settings(jwt_leeway_seconds=0)

    with pytest.raises(TokenExpiredError):
        decode_token(settings, _expired_ago(2, settings), TokenType.ACCESS)


def test_issue_time_in_the_future_follows_the_leeway() -> None:
    settings = _settings(jwt_leeway_seconds=5)
    soon = datetime.now(UTC) + timedelta(seconds=2)
    later = datetime.now(UTC) + timedelta(seconds=30)

    decode_token(
        settings,
        create_access_token(settings, uuid.uuid4(), now=soon).token,
        TokenType.ACCESS,
    )
    with pytest.raises(TokenInvalidError):
        decode_token(
            settings,
            create_access_token(settings, uuid.uuid4(), now=later).token,
            TokenType.ACCESS,
        )


# --- hash slots ----------------------------------------------------------


async def test_cancelled_hash_keeps_its_slot_until_the_thread_ends() -> None:
    release = threading.Event()
    started = threading.Semaphore(0)

    def blocker() -> int:
        started.release()
        release.wait(10)
        return 1

    holders = [
        asyncio.create_task(security._run_bounded(blocker))
        for _ in range(security.HASH_CONCURRENCY_LIMIT)
    ]
    for _ in holders:
        assert await asyncio.to_thread(started.acquire, True, 5)
    waiting = asyncio.create_task(security._run_bounded(lambda: "done"))
    await asyncio.sleep(0.05)
    assert not waiting.done()  # every slot is taken

    for holder in holders:
        holder.cancel()
    await asyncio.gather(*holders, return_exceptions=True)
    await asyncio.sleep(0.1)
    assert not waiting.done()  # cancelled, but the threads still run

    release.set()
    assert await asyncio.wait_for(waiting, 5) == "done"


async def test_dummy_hash_is_built_lazily_and_once() -> None:
    security._dummy_hash.cache_clear()
    assert security._dummy_hash.cache_info().currsize == 0

    await verify_dummy_password("whatever")
    await verify_dummy_password("whatever")

    info = security._dummy_hash.cache_info()
    assert info.currsize == 1 and info.misses == 1

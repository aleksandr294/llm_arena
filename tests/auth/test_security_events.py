"""Security events: names, fields and that no secret or hostile text gets
through. Need PostgreSQL (``DATABASE_URL``) for the flow tests."""

import logging

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from api.v1.utils import get_settings_dependency
from components.core.config import SettingsSchema
from components.core.enums import Role
from components.core.logging import JSONFormatter, log_security_event
from tests.auth.helpers import PASSWORD, Factory, seed_user
from tests.security.helpers import CORPUS, MARK, SAMPLE_IDS

LOGIN = "/api/v1/auth/login"
WRONG = "sentinel-wrong-password-4d2f"


def _events(
    caplog: pytest.LogCaptureFixture, name: str
) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == name]


def _assert_no_control(text: str) -> None:
    assert all(ord(c) >= 0x20 and ord(c) != 0x7F for c in text)


# --- the helper (no database) ---------------------------------------------


def test_helper_rejects_a_field_outside_the_allowlist() -> None:
    with pytest.raises(TypeError):
        log_security_event("auth.login_failed", email="a@example.com")
    with pytest.raises(TypeError):
        log_security_event("auth.login_failed", password="x")


def test_helper_omits_none_and_emits_one_json_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="security")

    log_security_event(
        "auth.login_failed", client_address="10.0.0.1", user_id=None
    )

    (record,) = _events(caplog, "auth.login_failed")
    assert record.client_address == "10.0.0.1"  # type: ignore[attr-defined]
    assert not hasattr(record, "user_id")
    assert "\n" not in JSONFormatter().format(record)


@pytest.mark.parametrize("sample_id", SAMPLE_IDS)
def test_helper_escapes_hostile_values(
    sample_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="security")
    hostile = CORPUS[sample_id]

    log_security_event("access.denied", client_address=hostile, reason=hostile)

    (record,) = _events(caplog, "access.denied")
    for value in (record.client_address, record.reason):  # type: ignore[attr-defined]
        _assert_no_control(value)
        assert len(value) <= 128
    line = JSONFormatter().format(record)
    assert "\n" not in line and "\r" not in line


# --- the flows (database) ----------------------------------------------------


@pytest.fixture
def _flows(api: None, app: FastAPI, settings: SettingsSchema) -> SettingsSchema:
    limited = settings.model_copy(update={"auth_max_failed_attempts": 2})
    app.dependency_overrides[get_settings_dependency] = lambda: limited
    return limited


@pytest.mark.usefixtures("_flows")
async def test_failed_login_event_has_address_and_no_secrets(
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await seed_user(session_factory, "Sentinel.Person@Example.com")
    caplog.set_level(logging.INFO)

    response = await client.post(
        LOGIN, json={"email": user.email, "password": WRONG}
    )

    assert response.status_code == 401
    (record,) = _events(caplog, "auth.login_failed")
    assert record.client_address == "127.0.0.1"  # type: ignore[attr-defined]
    assert record.outcome == "denied"  # type: ignore[attr-defined]
    for secret in (user.email, user.email.lower(), WRONG, PASSWORD):
        assert secret not in caplog.text


@pytest.mark.usefixtures("_flows")
async def test_lockout_event_is_logged(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    for _ in range(2):
        await client.post(
            LOGIN, json={"email": "nobody@example.com", "password": WRONG}
        )

    response = await client.post(
        LOGIN, json={"email": "nobody@example.com", "password": WRONG}
    )

    assert response.status_code == 429
    (record,) = _events(caplog, "auth.locked_out")
    assert record.client_address == "127.0.0.1"  # type: ignore[attr-defined]
    assert "nobody@example.com" not in caplog.text


@pytest.mark.usefixtures("_flows")
async def test_refresh_reuse_event_carries_the_user_id(
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await seed_user(session_factory)
    tokens = (
        await client.post(
            LOGIN, json={"email": user.email, "password": PASSWORD}
        )
    ).json()
    body = {"refresh_token": tokens["refresh_token"]}
    assert (
        await client.post("/api/v1/auth/refresh", json=body)
    ).status_code == 200
    caplog.set_level(logging.INFO)

    replay = await client.post("/api/v1/auth/refresh", json=body)

    assert replay.status_code == 401
    (record,) = _events(caplog, "auth.refresh_reuse")
    assert record.user_id == str(user.user_id)  # type: ignore[attr-defined]
    assert tokens["refresh_token"] not in caplog.text


@pytest.mark.usefixtures("_flows")
async def test_permission_denial_event(
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    staff = await seed_user(session_factory, role=Role.STAFF)
    login = await client.post(
        LOGIN, json={"email": staff.email, "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    caplog.set_level(logging.INFO)

    response = await client.get("/api/v1/organizations", headers=headers)

    assert response.status_code == 403
    (record,) = _events(caplog, "access.denied")
    assert record.user_id == str(staff.user_id)  # type: ignore[attr-defined]
    assert record.reason == "role"  # type: ignore[attr-defined]


@pytest.mark.usefixtures("_flows")
@pytest.mark.parametrize("sample_id", SAMPLE_IDS)
async def test_hostile_identifier_never_reaches_the_events(
    sample_id: str,
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hostile = CORPUS[sample_id].replace("\x00", "")
    caplog.set_level(logging.INFO)

    await client.post(LOGIN, json={"email": hostile, "password": WRONG})

    assert MARK not in caplog.text
    for record in _events(caplog, "auth.login_failed"):
        for key in ("client_address", "reason", "outcome"):
            _assert_no_control(str(getattr(record, key, "")))

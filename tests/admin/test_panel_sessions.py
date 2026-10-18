"""Session security of the panel: which routes need a session, and which
cookies are accepted.

fastadmin's session is a JWT (HS256, ``user_id`` and ``session_expired_at``)
in the ``admin_session_id`` cookie. Cookies are forged here with PyJWT, with
the panel secret (a stand-in for a leaked key) and with a wrong one.
"""

import base64
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from components.auth.security import hash_password
from components.core.enums import Role
from components.users.models import User
from components.users.repository import UserRepository
from tests.access import PASSWORD
from tests.admin.conftest import World
from tests.admin.panel_process import (
    ADMIN_SECRET,
    API,
    ROOT,
    Panel,
    child_environment,
)

pytestmark = pytest.mark.integration

COOKIE = "admin_session_id"
WRONG_SECRET = "a-different-admin-secret-0123456789abcdef-xyz"

# Routes under /admin/api that answer without a session, on purpose:
# - sign-in: it is how a session starts; it checks the credentials itself.
# - configuration: the sign-in page needs the branding and username field;
#   without a session it lists no models (asserted below).
PUBLIC_API = frozenset(
    {("POST", "/api/sign-in"), ("GET", "/api/configuration")}
)
# Non-API route: the HTML shell of the bundled frontend, no data.
PUBLIC_PAGES = frozenset({("GET", "/")})

_ROUTE_DUMP = """
import json
from fastadmin.api.frameworks.fastapi.api import router as api
from fastadmin.api.frameworks.fastapi.views import router as views
print(json.dumps([
    [m, r.path] for router in (api, views) for r in router.routes
    for m in sorted(r.methods)
]))
"""

# Request bodies that pass validation, so the 401 can only come from the
# missing session. A route without an entry fails the test.
BODIES: dict[str, Any] = {
    "/api/add/{model}": {},
    "/api/change/{model}/{id}": {},
    "/api/change-password/{id}": {"password": "x"},
    "/api/export/{model}": {"format": "CSV"},
    "/api/action/{model}/{action}": {"ids": []},
    "/api/widget-action/{model}/{widget_action}": {"query": []},
}
FILES = {"/api/upload-file/{model}/{field_name}": {"file": ("a.txt", "x")}}


def _installed_routes() -> list[tuple[str, str]]:
    """fastadmin's routes, read in a fresh interpreter: importing it here
    would freeze its environment for the whole test process."""
    out = subprocess.run(
        [sys.executable, "-c", _ROUTE_DUMP],
        cwd=ROOT,
        env=child_environment() | {"ADMIN_USER_MODEL": "User"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [(method, path) for method, path in json.loads(out)]


def _concrete(path: str) -> str:
    row = uuid.uuid4()
    return (
        path.replace("{model}", "Dataset")
        .replace("{id}", str(row))
        .replace("{action}", "x")
        .replace("{widget_action}", "x")
        .replace("{field_name}", "name")
    )


def test_every_panel_route_needs_a_session_unless_public() -> None:
    routes = _installed_routes()
    registered = set(routes)
    assert PUBLIC_API | PUBLIC_PAGES <= registered, "stale public entries"
    assert ("GET", "/api/list/{model}") in registered  # the walk found data
    panel = Panel()
    try:
        failures: list[str] = []
        checked = 0
        for method, path in routes:
            if (method, path) in PUBLIC_API | PUBLIC_PAGES:
                continue
            assert path.startswith("/api/"), f"unexpected page {path}"
            reply = panel.request(
                "anon",
                method,
                "/admin" + _concrete(path),
                json_body=BODIES.get(path),
                files=FILES.get(path),
            )
            checked += 1
            if reply.status != 401:
                failures.append(f"{method} {path} -> {reply.status}")
        assert failures == []
        assert checked >= 11
        # The public routes disclose nothing without a session.
        config = panel.get("anon", "/configuration")
        assert config.status == 200
        assert config.json()["models"] == []
        assert panel.request("anon", "GET", "/admin/").status == 200
    finally:
        panel.close()


def _cookie(
    user_id: object,
    *,
    secret: str = ADMIN_SECRET,
    expires: timedelta = timedelta(hours=1),
    claims: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "session_expired_at": (datetime.now(UTC) + expires).isoformat(),
    }
    payload.update(claims or {})
    return jwt.encode(payload, secret, algorithm="HS256")


def _as(cookie: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={cookie}"}


def _data_routes(panel: Panel, cookie: str, world: World) -> set[int]:
    """Status codes of the data routes for a request carrying ``cookie``."""
    row = world.datasets["ds_a"]
    paths = ("/me", "/list/Dataset", "/list/User", f"/retrieve/Dataset/{row}")
    return {
        panel.request("forged", "GET", API + path, headers=_as(cookie)).status
        for path in paths
    }


def _alg_none(user_id: object) -> str:
    def part(data: dict[str, Any]) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = part({"alg": "none", "typ": "JWT"})
    body = part(
        {
            "user_id": str(user_id),
            "session_expired_at": (
                datetime.now(UTC) + timedelta(hours=1)
            ).isoformat(),
        }
    )
    return f"{header}.{body}."


def test_the_forging_helper_makes_valid_cookies(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    """Control: a correctly signed, fresh cookie of an active user works, so
    the rejections below are caused by what is wrong with the cookie."""
    cookie = _cookie(world.principals["superadmin"].user_id)
    assert _data_routes(panel, cookie, world) == {200}


def test_cookies_not_signed_by_the_panel_are_rejected(
    panel: Panel, world: World, emails: dict[str, str]
) -> None:
    admin = world.principals["superadmin"].user_id
    assert panel.sign_in("real", emails["staff_a"], PASSWORD).status == 200
    genuine = panel.request("real", "GET", API + "/me")
    assert genuine.status == 200

    # Header.payload.signature of a real session, payload swapped for the
    # superadmin's id: the signature no longer matches.
    real_cookie = _sign_in_cookie(panel, emails["staff_a"])
    head, _, tail = real_cookie.split(".")
    forged_payload = _cookie(admin).split(".")[1]
    swapped = f"{head}.{forged_payload}.{tail}"
    flipped = real_cookie[:-3] + ("AAA" if real_cookie[-3:] != "AAA" else "BBB")
    cookies = {
        "wrong key": _cookie(admin, secret=WRONG_SECRET),
        "empty key": _cookie(admin, secret="x" * 32),
        "payload swapped": swapped,
        "signature flipped": flipped,
        "alg none": _alg_none(admin),
        "garbage": "not-a-jwt",
        "empty": "",
    }
    for label, cookie in cookies.items():
        assert _data_routes(panel, cookie, world) == {401}, label


def test_expired_and_incomplete_cookies_are_rejected(
    panel: Panel, world: World
) -> None:
    admin = world.principals["superadmin"].user_id
    cookies = {
        "expired": _cookie(admin, expires=timedelta(seconds=-5)),
        "no expiry claim": jwt.encode(
            {"user_id": str(admin)}, ADMIN_SECRET, algorithm="HS256"
        ),
        "no user claim": jwt.encode(
            {
                "session_expired_at": (
                    datetime.now(UTC) + timedelta(hours=1)
                ).isoformat()
            },
            ADMIN_SECRET,
            algorithm="HS256",
        ),
        "not a uuid": _cookie("1; DROP TABLE users"),
        "unknown user": _cookie(uuid.uuid4()),
    }
    for label, cookie in cookies.items():
        assert _data_routes(panel, cookie, world) == {401}, label


async def test_cookies_of_deleted_or_deactivated_users_are_rejected(
    panel: Panel,
    engine: AsyncEngine,
    world: World,
    emails: dict[str, str],
) -> None:
    # A user without data, so the row can be deleted.
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        doomed = await UserRepository(session).create(
            "doomed@example.com",
            await hash_password(PASSWORD),
            Role.STAFF,
            world.org_ids["a"],
        )
        await session.commit()
    staff = doomed.id
    deactivated = world.principals["staff_b"].user_id
    cookie_deleted = _sign_in_cookie(panel, "doomed@example.com")
    cookie_deactivated = _sign_in_cookie(panel, emails["staff_b"])
    # Signed in (a row of another organization is a plain 404).
    assert 401 not in _data_routes(panel, cookie_deleted, world)
    assert 401 not in _data_routes(panel, cookie_deactivated, world)

    async with engine.begin() as conn:
        await conn.execute(
            update(User).where(User.id == deactivated).values(is_active=False)
        )
        await conn.execute(delete(User).where(User.id == staff))
    # A genuine cookie, a freshly forged one: the account is what matters.
    for cookie in (
        cookie_deleted,
        cookie_deactivated,
        _cookie(staff),
        _cookie(deactivated),
    ):
        assert _data_routes(panel, cookie, world) == {401}


def _sign_in_cookie(panel: Panel, email: str) -> str:
    reply = panel.sign_in(f"c-{email}", email, PASSWORD)
    assert reply.status == 200
    header = reply.set_cookie[0]
    assert header.startswith(f"{COOKIE}=")
    return header.split(";", 1)[0].split("=", 1)[1]

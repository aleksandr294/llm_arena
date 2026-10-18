"""The panel behind a real uvicorn server.

``UserAdmin.get_obj`` tells "fastadmin is validating a session" from "a data
route reads a user" by whether a user is bound to the admin object, which
fastadmin keeps in context variables. That is only sound if one request's
binding never reaches the next. uvicorn runs every request in its own asyncio
task, whose context changes do not propagate back, so it holds even for
requests that reuse one keep-alive connection. This test pins that guarantee
with real sockets instead of the in-process transport.
"""

import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from components.users.models import User
from tests.access import PASSWORD
from tests.admin.conftest import World
from tests.admin.panel_process import API, ROOT, child_environment

pytestmark = pytest.mark.integration

COOKIE = "admin_session_id"


@pytest.fixture
def server() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=child_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/api/v1/health/live").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            raise AssertionError("uvicorn did not start")
        yield base
    finally:
        process.terminate()  # our own child, by handle
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def _sign_in(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    reply = await client.post(
        f"{API}/sign-in", json={"username": email, "password": PASSWORD}
    )
    assert reply.status_code == 200
    value = reply.cookies[COOKIE]
    client.cookies.clear()
    return {"Cookie": f"{COOKIE}={value}"}


async def test_one_requests_user_never_leaks_into_the_next(
    server: str,
    engine: AsyncEngine,
    world: World,
    emails: dict[str, str],
) -> None:
    async with httpx.AsyncClient(
        base_url=server
    ) as client:  # one keep-alive connection
        superadmin = await _sign_in(client, emails["superadmin"])
        staff = await _sign_in(client, emails["staff_a"])
        everything = (
            await client.get(
                f"{API}/list/Dataset", headers=superadmin, params={"limit": 100}
            )
        ).json()["total"]

        async with engine.begin() as conn:
            await conn.execute(
                update(User)
                .where(User.id == world.principals["staff_a"].user_id)
                .values(is_active=False)
            )
        for _ in range(3):
            # A superadmin request first: were its user still bound, the
            # next session validation would be answered for the wrong
            # caller (and a deactivated account would pass).
            assert (
                await client.get(f"{API}/me", headers=superadmin)
            ).status_code == 200
            for path in ("/me", "/list/Dataset", "/list/User"):
                reply = await client.get(f"{API}{path}", headers=staff)
                assert reply.status_code == 401, path

        async with engine.begin() as conn:
            await conn.execute(
                update(User)
                .where(User.id == world.principals["staff_a"].user_id)
                .values(is_active=True)
            )
        # And the other direction: a staff request must not narrow or widen
        # the superadmin's next one.
        mine = (await client.get(f"{API}/list/Dataset", headers=staff)).json()[
            "total"
        ]
        assert 0 < mine < everything
        again = (
            await client.get(f"{API}/list/Dataset", headers=superadmin)
        ).json()
        assert again["total"] == everything

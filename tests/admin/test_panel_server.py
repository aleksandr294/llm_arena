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
import tempfile
import time
from collections.abc import Iterator
from typing import IO

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from components.users.models import User
from tests.access import PASSWORD
from tests.admin.helpers import World
from tests.admin.panel_process import API, ROOT, child_environment

COOKIE = "admin_session_id"
READY_TIMEOUT = 30.0


@pytest.fixture
def server() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryFile() as child_output:
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.admin.uvicorn_child", str(port)],
            cwd=ROOT,
            env=child_environment(),
            stdout=child_output,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_until_ready(base, process, child_output)
            yield base
        finally:
            process.terminate()  # our own child, by handle
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _wait_until_ready(
    base: str, process: "subprocess.Popen[bytes]", child_output: IO[bytes]
) -> None:
    """Poll the child until it answers or ``READY_TIMEOUT`` passes; the
    failure message carries what the child printed."""
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            if httpx.get(f"{base}/api/v1/health/live").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    child_output.seek(0)
    output = child_output.read().decode(errors="replace")
    state = (
        f"exited with {process.returncode}"
        if process.poll() is not None
        else f"not ready after {READY_TIMEOUT}s"
    )
    raise AssertionError(f"uvicorn child {state}; output:\n{output}")


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

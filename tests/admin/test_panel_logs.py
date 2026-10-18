"""Nothing sensitive reaches the logs while the panel is used."""

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from components.datasets.models import TestCase
from components.experiments.models import ExperimentResult
from components.users.models import User
from tests.access import PASSWORD
from tests.admin.conftest import World
from tests.admin.panel_process import API, Panel

pytestmark = pytest.mark.integration

INPUT = "SENTINEL-CASE-INPUT-7f3a"
EXPECTED = "SENTINEL-EXPECTED-91bc"
RESPONSE = "SENTINEL-RESPONSE-2d5e"
PROMPT = "SENTINEL-PROMPT-c04f"
WRONG_PASSWORD = "SENTINEL-WRONG-PASSWORD-b8d2"


async def test_nothing_sensitive_reaches_debug_logs(
    engine: AsyncEngine, world: World, emails: dict[str, str]
) -> None:
    """Passwords, session cookies, password hashes and row content stay out
    of DEBUG logs when signing in, failing to sign in and browsing."""
    async with engine.begin() as conn:
        await conn.execute(
            update(TestCase).values(input=INPUT, expected_output=EXPECTED)
        )
        await conn.execute(
            update(ExperimentResult).values(
                response_text=RESPONSE, rendered_prompt=PROMPT
            )
        )
        hashes = list(
            (await conn.execute(select(User.password_hash))).scalars()
        )
    assert hashes

    panel = Panel(debug_logs=True)
    secrets = [PASSWORD, WRONG_PASSWORD, *hashes]
    seen = ""
    try:
        for name in ("staff_a", "admin_a", "superadmin"):
            bad = panel.sign_in(name, emails[name], WRONG_PASSWORD)
            assert bad.status == 401
            unknown = panel.sign_in(name, "nobody@example.com", WRONG_PASSWORD)
            assert unknown.status == 401
            reply = panel.sign_in(name, emails[name], PASSWORD)
            assert reply.status == 200
            cookie = reply.set_cookie[0].split(";", 1)[0].split("=", 1)[1]
            assert cookie
            secrets.append(cookie)
            # A tampered cookie is not a secret of ours, but must not be
            # echoed either.
            secrets.append("SENTINEL-GARBAGE-COOKIE-1e77")
            panel.request(
                "garbage",
                "GET",
                f"{API}/me",
                headers={
                    "Cookie": "admin_session_id=SENTINEL-GARBAGE-COOKIE-1e77"
                },
            )
            for model in (
                "Dataset",
                "TestCase",
                "Experiment",
                "ExperimentResult",
                "EvaluationResult",
                "User",
            ):
                listing = panel.get(name, f"/list/{model}", limit=50)
                assert listing.status == 200, (name, model)
                for row in listing.json()["results"][:3]:
                    seen += panel.get(
                        name, f"/retrieve/{model}/{row['id']}"
                    ).text
            panel.get(name, "/me")
            panel.get(name, "/configuration")
    finally:
        logs = panel.close()

    # The test really browsed the content it protects ...
    assert RESPONSE in seen and INPUT in seen
    # ... capture works, and none of it was logged.
    assert "canary" in logs
    assert "Admin panel mounted" in logs  # the app's own loggers too
    for secret in [*secrets, INPUT, EXPECTED, RESPONSE, PROMPT]:
        assert secret not in logs

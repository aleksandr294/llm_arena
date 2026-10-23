"""Mounting the panel.

fastadmin reads its environment once, at import. The in-process tests below
therefore never mount the panel; anything that imports or mounts it runs in
a fresh interpreter (``_run``), so one test's settings cannot leak into
another and "not imported when off" can be observed for real.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.routing import Mount

from admin import _export_environment, mount_admin
from api.app import create_app
from components.core.config import SettingsSchema, get_settings
from conftest import TEST_JWT_SECRET

ROOT = Path(__file__).resolve().parents[2]
ADMIN_SECRET = "admin-secret-for-tests-0123456789abcdef-xyz"

PRELUDE = f"""
import asyncio, json, sys
from httpx import ASGITransport, AsyncClient
from api.app import create_app
from components.core.config import SettingsSchema

def settings(**extra):
    return SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key={TEST_JWT_SECRET!r},
        _env_file=None,
        **extra,
    )

async def get(app, path):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        return await c.get(path)
"""


def _run(code: str, tmp_path: Path, **env: str) -> dict[str, object]:
    """Run ``code`` in a clean interpreter; it prints one JSON object."""
    import os

    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ADMIN_", "DATABASE_", "RABBITMQ_"))
    }
    clean.update(env)
    clean["PYTHONPATH"] = f"{tmp_path}{os.pathsep}{ROOT}"
    done = subprocess.run(
        [sys.executable, "-c", PRELUDE + textwrap.dedent(code)],
        cwd=ROOT,
        env=clean,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    result: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    return result


def test_mount_is_skipped_without_secret_and_does_not_import_fastadmin(
    tmp_path: Path,
) -> None:
    result = _run(
        """
        app = create_app(settings())
        r = asyncio.run(get(app, "/admin"))
        r2 = asyncio.run(get(app, "/admin/api/configuration"))
        print(json.dumps({
            "admin": r.status_code,
            "api": r2.status_code,
            "imported": any(
                m.split(".")[0] == "fastadmin" for m in sys.modules
            ),
            "admin_core": "admin.core" in sys.modules,
        }))
        """,
        tmp_path,
    )
    assert result == {
        "admin": 404,
        "api": 404,
        "imported": False,
        "admin_core": False,
    }


async def test_app_without_secret_answers_404_for_admin(
    app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/admin")).status_code == 404
        assert (await client.get("/admin/")).status_code == 404
        assert (await client.get("/api/v1/health/live")).status_code == 200


def test_mount_with_secret_configures_fastadmin_before_import(
    tmp_path: Path,
) -> None:
    (tmp_path / "probe_admin_module.py").write_text(textwrap.dedent("""
            import fastadmin.settings as fs

            SEEN = {
                "key": fs.settings.ADMIN_SECRET_KEY,
                "model": fs.settings.ADMIN_USER_MODEL,
                "username": fs.settings.ADMIN_USER_MODEL_USERNAME_FIELD,
                "secure": fs.settings.ADMIN_SESSION_COOKIE_SECURE,
            }
            """))
    result = _run(
        f"""
        import admin
        admin.ADMIN_MODULES.append("probe_admin_module")
        app = create_app(settings(
            admin_secret_key={ADMIN_SECRET!r},
            admin_session_cookie_secure=False,
        ))
        import probe_admin_module as probe
        index = asyncio.run(get(app, "/admin/"))
        schema = asyncio.run(get(app, "/openapi.json")).json()
        print(json.dumps({{
            "seen_key": probe.SEEN["key"] == {ADMIN_SECRET!r},
            "model": probe.SEEN["model"],
            "username": probe.SEEN["username"],
            "secure": probe.SEEN["secure"],
            "index": index.status_code,
            "paths": [p for p in schema["paths"] if "admin" in p],
        }}))
        """,
        tmp_path,
    )
    assert result == {
        "seen_key": True,
        "model": "User",
        "username": "email",
        "secure": False,
        "index": 200,
        "paths": [],
    }


def test_cookie_secure_defaults_to_true(tmp_path: Path) -> None:
    result = _run(
        f"""
        create_app(settings(admin_secret_key={ADMIN_SECRET!r}))
        import fastadmin.settings as fs
        print(json.dumps({{"secure": fs.settings.ADMIN_SESSION_COOKIE_SECURE}}))
        """,
        tmp_path,
    )
    assert result == {"secure": True}


def test_second_mount_reuses_the_single_fastadmin_import(
    tmp_path: Path,
) -> None:
    result = _run(
        f"""
        s = settings(admin_secret_key={ADMIN_SECRET!r})
        first = create_app(s)
        import fastadmin
        module = fastadmin.fastapi_app
        second = create_app(s)
        print(json.dumps({{"same": fastadmin.fastapi_app is module}}))
        """,
        tmp_path,
    )
    assert result == {"same": True}


def test_fastadmin_imported_before_mount_is_rejected(tmp_path: Path) -> None:
    result = _run(
        f"""
        import fastadmin  # imported without our settings
        try:
            create_app(settings(admin_secret_key={ADMIN_SECRET!r}))
            outcome = "mounted"
        except RuntimeError as exc:
            outcome = "rejected"
            assert {ADMIN_SECRET!r} not in str(exc)
        print(json.dumps({{"outcome": outcome}}))
        """,
        tmp_path,
    )
    assert result == {"outcome": "rejected"}


def test_rejected_mount_leaves_the_environment_untouched(
    tmp_path: Path,
) -> None:
    result = _run(
        f"""
        import fastadmin  # imported without our settings
        import os
        before = dict(os.environ)
        try:
            create_app(settings(admin_secret_key={ADMIN_SECRET!r}))
        except RuntimeError:
            pass
        print(json.dumps({{"unchanged": dict(os.environ) == before}}))
        """,
        tmp_path,
    )
    assert result == {"unchanged": True}


def test_mount_exports_session_ttl_and_pins_samesite(tmp_path: Path) -> None:
    result = _run(
        f"""
        import os
        create_app(settings(
            admin_secret_key={ADMIN_SECRET!r},
            admin_session_ttl_seconds=600,
        ))
        import fastadmin.settings as fs
        print(json.dumps({{
            "ttl": fs.settings.ADMIN_SESSION_EXPIRED_AT,
            "samesite": fs.settings.ADMIN_SESSION_COOKIE_SAMESITE,
        }}))
        """,
        tmp_path,
        ADMIN_SESSION_COOKIE_SAMESITE="none",
    )
    assert result == {"ttl": 600, "samesite": "lax"}


@pytest.mark.parametrize(
    ("secret", "jwt_equal"),
    [("short-admin-secret", False), (TEST_JWT_SECRET, True)],
)
def test_invalid_admin_secret_fails_startup_without_echo(
    monkeypatch: MonkeyPatch, secret: str, jwt_equal: bool
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:pw@h/db")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://u:pw@h/")
    monkeypatch.setenv("ADMIN_SECRET_KEY", secret)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError) as exc:
            create_app()
    finally:
        get_settings.cache_clear()

    message = str(exc.value)
    assert "admin_secret_key" in message
    assert secret not in message
    assert jwt_equal == ("differ" in message)


def test_export_environment_requires_the_admin_secret() -> None:
    settings = SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
        admin_secret_key=None,
        _env_file=None,
    )
    with pytest.raises(RuntimeError, match="ADMIN_SECRET_KEY is not set"):
        _export_environment(settings)


def test_mount_admin_without_secret_returns_false() -> None:
    settings = SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
        admin_secret_key=None,
        _env_file=None,
    )
    app = FastAPI()
    assert mount_admin(app, settings) is False
    assert not [r for r in app.routes if isinstance(r, Mount)]

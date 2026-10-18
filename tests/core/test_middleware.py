"""Security headers, ``no-store`` and the request body limit."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient, Response
from starlette.types import Message, Receive, Scope, Send

from api.app import create_app
from api.middleware import CSP_STRICT, SecurityMiddleware
from components.core.config import SettingsSchema
from conftest import TEST_JWT_SECRET

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 100
SECURITY = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def _settings(**overrides: Any) -> SettingsSchema:
    return SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
        admin_secret_key=None,
        _env_file=None,
        **overrides,
    )


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _probe_app(**options: Any) -> Any:
    """A tiny app that reads and reports the body, behind the middleware."""
    inner = FastAPI()

    @inner.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @inner.get("/plain")
    async def plain() -> dict[str, bool]:
        return {"ok": True}

    @inner.post("/admin/api/sign-in")
    async def admin_sign_in() -> dict[str, bool]:
        return {"ok": True}

    @inner.get("/api/v1/auth/anything")
    async def auth() -> dict[str, bool]:
        return {"ok": True}

    # Same wiring as create_app: inside Starlette's error middleware.
    inner.add_middleware(SecurityMiddleware, max_body_bytes=LIMIT, **options)
    return inner


def _assert_security_headers(response: Response) -> None:
    for name, value in SECURITY.items():
        assert response.headers[name] == value
    assert "content-security-policy" in response.headers


async def test_headers_on_success_and_error_responses() -> None:
    app = create_app(_settings())
    async with _client(app) as client:
        ok = await client.get("/api/v1/health/live")
        missing = await client.get("/api/v1/no-such-route")
        unauthorized = await client.get("/api/v1/datasets")
    assert (ok.status_code, missing.status_code) == (200, 404)
    assert unauthorized.status_code == 401
    for response in (ok, missing, unauthorized):
        _assert_security_headers(response)
        assert "strict-transport-security" not in response.headers


async def test_non_html_responses_get_the_strict_policy() -> None:
    app = create_app(_settings(max_request_body_bytes=50))
    async with _client(app) as client:
        ok = await client.get("/api/v1/health/live")
        missing = await client.get("/api/v1/no-such-route")
        spec = await client.get("/openapi.json")
        too_large = await client.post("/api/v1/auth/login", content=b"x" * 51)
    assert too_large.status_code == 413
    for response in (ok, missing, spec, too_large):
        assert response.headers["content-security-policy"] == CSP_STRICT
    assert "default-src 'none'" in CSP_STRICT
    assert "frame-ancestors 'none'" in CSP_STRICT


async def test_hsts_only_when_enabled() -> None:
    app = create_app(_settings(hsts_max_age_seconds=31536000))
    async with _client(app) as client:
        response = await client.get("/api/v1/health/live")
        error = await client.get("/api/v1/no-such-route")
    for r in (response, error):
        assert r.headers["strict-transport-security"] == "max-age=31536000"


def test_settings_validation() -> None:
    with pytest.raises(ValueError, match="greater than"):
        _settings(hsts_max_age_seconds=-1)
    with pytest.raises(ValueError, match="greater than"):
        _settings(max_request_body_bytes=0)
    defaults = _settings()
    assert defaults.hsts_max_age_seconds == 0
    assert defaults.max_request_body_bytes == 2 * 1024 * 1024


async def test_no_store_rules() -> None:
    app = _probe_app()
    async with _client(app) as client:
        plain = await client.get("/plain")
        auth = await client.get("/api/v1/auth/anything")
        bearer = await client.get(
            "/plain", headers={"Authorization": "Bearer x"}
        )
        cookie = await client.get(
            "/plain", headers={"Cookie": "a=b; admin_session_id=zzz"}
        )
        other_cookie = await client.get(
            "/plain", headers={"Cookie": "theme=dark"}
        )
        lookalike = await client.get(
            "/plain", headers={"Cookie": "x_admin_session_id=1"}
        )
    assert "cache-control" not in plain.headers
    assert "cache-control" not in other_cookie.headers
    assert "cache-control" not in lookalike.headers
    for response in (auth, bearer, cookie):
        assert response.headers["cache-control"] == "no-store"


async def test_admin_sign_in_response_is_no_store() -> None:
    async with _client(_probe_app()) as client:
        sign_in = await client.post("/admin/api/sign-in")
        other = await client.get("/plain")
    assert sign_in.headers["cache-control"] == "no-store"
    assert "cache-control" not in other.headers


async def test_auth_paths_are_no_store_in_the_real_app() -> None:
    app = create_app(_settings())
    async with _client(app) as client:
        health = await client.get("/api/v1/health/live")
        validate = await client.post("/api/v1/auth/validate")
        bearer = await client.get(
            "/api/v1/health/live", headers={"Authorization": "Bearer x"}
        )
    assert "cache-control" not in health.headers
    assert validate.headers["cache-control"] == "no-store"
    assert bearer.headers["cache-control"] == "no-store"


async def test_declared_length_over_limit_is_rejected_early() -> None:
    app = _probe_app()
    async with _client(app) as client:
        response = await client.post("/echo", content=b"x" * (LIMIT + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    _assert_security_headers(response)


async def test_declared_rejection_never_reaches_the_app() -> None:
    reached = False

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal reached
        reached = True

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        raise AssertionError("body must not be read")

    scope: Scope = {
        "type": "http",
        "path": "/echo",
        "headers": [(b"content-length", str(LIMIT + 1).encode())],
    }
    await SecurityMiddleware(inner, max_body_bytes=LIMIT)(scope, receive, send)
    assert not reached
    assert sent[0]["status"] == 413


async def test_exactly_the_limit_is_allowed() -> None:
    app = _probe_app()
    async with _client(app) as client:
        response = await client.post("/echo", content=b"x" * LIMIT)
        small = await client.post("/echo", content=b"hi")
        empty = await client.post("/echo")
    assert response.status_code == 200 and response.json() == {"size": LIMIT}
    assert small.json() == {"size": 2}
    assert empty.json() == {"size": 0}
    _assert_security_headers(response)


async def _chunks(sizes: list[int]) -> AsyncIterator[bytes]:
    for size in sizes:
        yield b"x" * size


async def test_chunked_body_over_limit_is_rejected() -> None:
    app = _probe_app()
    async with _client(app) as client:
        response = await client.post(
            "/echo", content=_chunks([40, 40, 21]), headers={}
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    _assert_security_headers(response)


async def test_chunked_body_at_the_limit_is_allowed() -> None:
    app = _probe_app()
    async with _client(app) as client:
        response = await client.post("/echo", content=_chunks([40, 40, 20]))
    assert response.status_code == 200
    assert response.json() == {"size": LIMIT}


async def test_chunked_overflow_stops_before_more_is_read() -> None:
    reads = 0

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if not message.get("more_body"):
                break

    async def receive() -> Message:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"x" * 60, "more_body": True}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "path": "/x", "headers": []}
    await SecurityMiddleware(inner, max_body_bytes=LIMIT)(scope, receive, send)
    assert reads == 2
    assert sent[0]["status"] == 413


async def test_non_http_scopes_pass_through_untouched() -> None:
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)
        await send({"type": f"{scope['type']}.done"})

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    middleware = SecurityMiddleware(inner, max_body_bytes=1)
    for kind in ("lifespan", "websocket"):
        await middleware({"type": kind}, receive, send)
    assert [s["type"] for s in seen] == ["lifespan", "websocket"]
    assert sent == [{"type": "lifespan.done"}, {"type": "websocket.done"}]


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_documentation_still_loads(path: str) -> None:
    async with _client(create_app(_settings())) as client:
        response = await client.get(path)
    assert response.status_code == 200
    _assert_security_headers(response)


async def test_normal_api_request_body_is_unaffected() -> None:
    # Reaches validation (422), not the size limit.
    async with _client(create_app(_settings())) as client:
        response = await client.post(
            "/api/v1/auth/login", json={"email": "not-an-email"}
        )
    assert response.status_code == 422


async def test_oversized_api_request_is_413_with_the_api_error_shape() -> None:
    app = create_app(_settings(max_request_body_bytes=50))
    async with _client(app) as client:
        response = await client.post("/api/v1/auth/login", content=b"x" * 51)
    assert response.status_code == 413
    assert list(response.json()) == ["detail"]


async def test_chunked_oversized_request_in_the_real_app() -> None:
    app = create_app(_settings(max_request_body_bytes=50))
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/auth/login", content=_chunks([30, 30])
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    _assert_security_headers(response)


def test_admin_panel_pages_load_with_the_headers(tmp_path: Path) -> None:
    code = textwrap.dedent(f"""
        import asyncio, json
        from httpx import ASGITransport, AsyncClient
        from api.app import create_app
        from components.core.config import SettingsSchema

        app = create_app(SettingsSchema(
            database_url="postgresql+asyncpg://unused/unused",
            rabbitmq_url="amqp://unused/",
            jwt_secret_key={TEST_JWT_SECRET!r},
            admin_secret_key="admin-secret-for-tests-0123456789abcdef-xyz",
            _env_file=None,
        ))

        async def go():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                r = await c.get("/admin/")
                big = await c.post("/admin/api/sign-in", content=b"x" * 3000000)
                return r, big

        r, big = asyncio.run(go())
        print(json.dumps({{
            "index": r.status_code,
            "nosniff": r.headers.get("x-content-type-options"),
            "frame": r.headers.get("x-frame-options"),
            "big": big.status_code,
        }}))
        """)
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ADMIN_", "DATABASE_", "RABBITMQ_"))
    }
    clean["PYTHONPATH"] = str(ROOT)
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=clean,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {
        "index": 200,
        "nosniff": "nosniff",
        "frame": "DENY",
        "big": 413,
    }


# Which CSP directive governs each kind of resource tag.
_SOURCE_TAGS = (
    ("script-src", re.compile(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", re.I)),
    (
        "style-src",
        re.compile(
            r"<link\b(?=[^>]*rel=\"stylesheet\")[^>]*\bhref=\"([^\"]+)\"",
            re.I | re.S,
        ),
    ),
)
_SCRIPT_BLOCK = re.compile(
    r"<script(?![^>]*\ssrc\s*=)[^>]*>(.*?)</script>", re.I | re.S
)


def _directive(policy: str, name: str) -> list[str]:
    for part in policy.split(";"):
        tokens = part.split()
        if tokens and tokens[0] == name:
            return tokens[1:]
    fallback = [p.split()[1:] for p in policy.split(";") if "default-src" in p]
    return fallback[0] if fallback else []


def assert_page_is_covered(html: str, policy: str) -> None:
    """Every external script/stylesheet origin and every inline script of the
    page is allowed by the policy; nothing is allowed inline by accident."""
    for directive, pattern in _SOURCE_TAGS:
        allowed = _directive(policy, directive)
        for src in pattern.findall(html):
            if src.startswith("/"):
                assert "'self'" in allowed, (src, policy)
                continue
            origin = "/".join(src.split("/")[:3])
            assert origin in allowed, (src, policy)
    scripts = _directive(policy, "script-src")
    assert "'unsafe-inline'" not in scripts
    assert "'unsafe-eval'" not in scripts
    for body in _SCRIPT_BLOCK.findall(html):
        if body.strip():
            digest = base64.b64encode(
                hashlib.sha256(body.encode()).digest()
            ).decode()
            assert f"'sha256-{digest}'" in scripts, policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src" in policy or "default-src 'none'" in policy


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_documentation_pages_policy_covers_their_sources(
    path: str,
) -> None:
    async with _client(create_app(_settings())) as client:
        response = await client.get(path)
    policy = response.headers["content-security-policy"]
    assert policy != CSP_STRICT
    assert "https://cdn.jsdelivr.net" in policy
    assert_page_is_covered(response.text, policy)


async def test_swagger_policy_hashes_the_inline_bootstrap_script() -> None:
    async with _client(create_app(_settings())) as client:
        page = await client.get("/docs")
        redirect = await client.get("/docs/oauth2-redirect")
    assert "'sha256-" in page.headers["content-security-policy"]
    assert redirect.status_code == 200
    assert_page_is_covered(
        redirect.text,
        redirect.headers["content-security-policy"],
    )


def test_admin_panel_policy_covers_its_sources(tmp_path: Path) -> None:
    code = textwrap.dedent(f"""
        import asyncio, json
        from httpx import ASGITransport, AsyncClient
        from api.app import create_app
        from components.core.config import SettingsSchema

        app = create_app(SettingsSchema(
            database_url="postgresql+asyncpg://unused/unused",
            rabbitmq_url="amqp://unused/",
            jwt_secret_key={TEST_JWT_SECRET!r},
            admin_secret_key="admin-secret-for-tests-0123456789abcdef-xyz",
            _env_file=None,
        ))

        async def go():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                page = await c.get("/admin/")
                js = await c.get("/admin/static/index.min.js")
                return page, js

        page, js = asyncio.run(go())
        print(json.dumps({{
            "status": page.status_code,
            "policy": page.headers.get("content-security-policy"),
            "html": page.text,
            "js_status": js.status_code,
            "js_policy": js.headers.get("content-security-policy"),
        }}))
        """)
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ADMIN_", "DATABASE_", "RABBITMQ_"))
    }
    clean["PYTHONPATH"] = str(ROOT)
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=clean,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["status"] == 200
    assert result["policy"] != CSP_STRICT
    assert "'sha256-" in result["policy"]
    assert_page_is_covered(result["html"], result["policy"])
    # Static assets are not documents: they get the strict policy.
    assert result["js_status"] == 200
    assert result["js_policy"] == CSP_STRICT


async def test_unhandled_error_is_a_generic_500_with_the_hardening_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner = FastAPI()

    @inner.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret-internal-detail /srv/app/file.py")

    inner.add_middleware(
        SecurityMiddleware, max_body_bytes=LIMIT, hsts_max_age_seconds=60
    )
    with caplog.at_level("ERROR", logger="api.middleware"):
        async with _client(inner) as client:
            response = await client.get(
                "/boom", headers={"Authorization": "Bearer x"}
            )
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert "secret-internal-detail" not in response.text
    _assert_security_headers(response)
    assert response.headers["content-security-policy"] == CSP_STRICT
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["strict-transport-security"] == "max-age=60"
    # The cause is still logged server-side, with its traceback.
    records = [r for r in caplog.records if r.name == "api.middleware"]
    assert records and records[0].exc_info is not None
    assert "secret-internal-detail" in str(records[0].exc_info[1])


async def test_error_after_the_response_started_is_not_masked() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        raise RuntimeError("late")

    wrapped = SecurityMiddleware(app, max_body_bytes=LIMIT)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {"type": "http", "path": "/x", "headers": []}
    with pytest.raises(RuntimeError, match="late"):
        await wrapped(scope, receive, send)
    assert sent[0]["status"] == 200


@pytest.mark.parametrize("content_type", [None, b"text/html"])
async def test_response_messages_may_omit_headers_and_body(
    content_type: bytes | None,
) -> None:
    """ASGI lets an app leave out ``headers`` on the start message and
    ``body`` on a body message; the middleware must not assume them."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        start: Message = {"type": "http.response.start", "status": 204}
        if content_type is not None:
            start["headers"] = [(b"content-type", content_type)]
        await send(start)
        await send({"type": "http.response.body"})

    wrapped = SecurityMiddleware(app, max_body_bytes=LIMIT)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    # /docs makes the html content type take the buffering path.
    scope = {"type": "http", "path": "/docs", "headers": []}
    await wrapped(scope, receive, send)
    start = sent[0]
    assert start["status"] == 204
    names = {name.decode().lower() for name, _ in start["headers"]}
    assert "content-security-policy" in names
    assert "x-content-type-options" in names

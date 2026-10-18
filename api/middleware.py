"""Pure ASGI hardening middleware: security headers, a Content Security
Policy, ``no-store`` for sensitive responses and a request body size limit.

Written as plain ASGI rather than ``BaseHTTPMiddleware``, which interferes
with streaming responses and background work. Values come in through the
constructor; nothing here reads settings.
"""

import base64
import hashlib
import json
import logging
import re
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# fastadmin's default session cookie name (ADMIN_SESSION_ID_KEY).
ADMIN_SESSION_COOKIE: Final = "admin_session_id"
AUTH_PATH_PREFIX: Final = "/api/v1/auth"
# fastadmin's credential endpoints; the session cookie is only sent after
# them, so the sign-in response itself must not be cached either.
ADMIN_AUTH_PATHS: Final = ("/admin/api/sign-in", "/admin/api/sign-out")
SECURITY_HEADERS: Final = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
)
TOO_LARGE_DETAIL: Final = "Request body too large"
INTERNAL_ERROR_DETAIL: Final = "Internal Server Error"

# Content Security Policy. Responses that are not documents (JSON, CSV,
# static assets, errors) get the strictest policy: nothing may load or be
# framed. Only the HTML pages below need more, and each gets the tightest
# policy that lets it work; the sources were taken from the served HTML
# (tests/core/test_middleware.py checks every script and stylesheet origin
# against these policies).
_CDN: Final = "https://cdn.jsdelivr.net"
_LOCKDOWN: Final = "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
CSP_STRICT: Final = f"default-src 'none'; {_LOCKDOWN}"
# {inline} is replaced by the hashes of the page's own inline scripts (see
# ``_inline_script_hashes``), so no page needs ``'unsafe-inline'`` scripts.
# Styles do need ``'unsafe-inline'``: Swagger UI and the panel's UI library
# set style attributes and inject style elements, and CSP cannot allow
# those by hash. Script execution is what the policy protects.
CSP_SWAGGER_UI: Final = (
    f"default-src 'none'; script-src {_CDN}{{inline}}; "
    f"style-src {_CDN} 'unsafe-inline'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    f"font-src 'self' data:; connect-src 'self'; {_LOCKDOWN}"
)
# ReDoc renders in a web worker created from a blob: URL and loads its
# fonts from Google Fonts.
CSP_REDOC: Final = (
    f"default-src 'none'; script-src {_CDN}{{inline}}; "
    f"style-src https://fonts.googleapis.com 'unsafe-inline'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src https://fonts.gstatic.com; worker-src blob:; "
    f"connect-src 'self'; {_LOCKDOWN}"
)
CSP_ADMIN_PANEL: Final = (
    "default-src 'none'; script-src 'self'{inline}; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    f"font-src 'self' data:; connect-src 'self'; {_LOCKDOWN}"
)
# (path prefix, policy) for HTML responses; first match wins. Prefix
# matching includes sub-pages such as ``/docs/oauth2-redirect``.
HTML_POLICIES: Final = (
    ("/docs", CSP_SWAGGER_UI),
    ("/redoc", CSP_REDOC),
    ("/admin", CSP_ADMIN_PANEL),
)
_INLINE_SCRIPT: Final = re.compile(
    r"<script(?![^>]*\ssrc\s*=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)


class _BodyTooLargeError(Exception):
    """Raised inside the wrapped ``receive`` once the limit is exceeded."""


class SecurityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        hsts_max_age_seconds: int = 0,
        admin_cookie_name: str = ADMIN_SESSION_COOKIE,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.hsts_max_age_seconds = hsts_max_age_seconds
        self.admin_cookie_name = admin_cookie_name

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        no_store = self._wants_no_store(scope["path"], headers)
        started = False
        exceeded = False
        # The app may turn the exception raised by ``receive`` into its own
        # error response (FastAPI answers 400 for an unreadable body), so
        # once the limit is exceeded any app response is replaced by 413.
        replaced = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started, replaced
            if replaced:
                return
            if exceeded and not started:
                replaced = True
                await self._too_large(send_wrapper_raw)
                return
            await send_wrapper_raw(message)

        held_start: Message | None = None
        held_policy = ""
        held_body: list[bytes] = []

        async def send_wrapper_raw(message: Message) -> None:
            nonlocal started, held_start, held_policy
            if message["type"] == "http.response.start":
                started = True
                template = self._html_policy(scope["path"], message)
                if template is not None:
                    # The page's inline scripts are hashed into its policy,
                    # which needs the whole (small) body first.
                    held_start, held_policy = message, template
                    return
                self._decorate(message, CSP_STRICT, no_store)
            elif held_start is not None:
                held_body.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(held_body)
                start, held_start = held_start, None
                self._decorate(
                    start, self._with_hashes(held_policy, body), no_store
                )
                await send(start)
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        if self._declared_length(headers) > self.max_body_bytes:
            await self._too_large(send_wrapper_raw)
            return

        received = 0

        async def receive_wrapper() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    exceeded = True
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except _BodyTooLargeError:
            pass
        except Exception:
            # Starlette's own 500 handler sits outside this middleware, so
            # an unhandled error would answer without the security headers
            # and CSP. Answer here instead, with no detail; the traceback
            # goes to the server log only.
            if started:
                raise
            logger.exception("Unhandled error while handling a request")
            if not exceeded:
                await self._internal_error(send_wrapper_raw)
        # Too late to change a response that already started.
        if exceeded and not started:
            await self._too_large(send_wrapper_raw)

    def _decorate(self, message: Message, policy: str, no_store: bool) -> None:
        # ASGI allows a response start without a headers list.
        message.setdefault("headers", [])
        out = MutableHeaders(scope=message)
        for name, value in SECURITY_HEADERS:
            out[name] = value
        out["Content-Security-Policy"] = policy
        if self.hsts_max_age_seconds > 0:
            out["Strict-Transport-Security"] = (
                f"max-age={self.hsts_max_age_seconds}"
            )
        if no_store:
            out["Cache-Control"] = "no-store"

    @staticmethod
    def _html_policy(path: str, start: Message) -> str | None:
        """The page policy template for an HTML response, else ``None``."""
        content_type = Headers(raw=start.get("headers", [])).get(
            "content-type", ""
        )
        if not content_type.lower().startswith("text/html"):
            return None
        for prefix, policy in HTML_POLICIES:
            if path == prefix or path.startswith(prefix + "/"):
                return policy
        return None

    @staticmethod
    def _with_hashes(policy: str, body: bytes) -> str:
        """Allow exactly the inline scripts present in ``body`` (by SHA-256),
        nothing else inline. The HTML is generated by FastAPI and
        fastadmin, not by users."""
        hashes = ""
        for match in _INLINE_SCRIPT.finditer(body.decode(errors="replace")):
            script = match.group(1)
            if script.strip():
                digest = hashlib.sha256(script.encode()).digest()
                hashes += f" 'sha256-{base64.b64encode(digest).decode()}'"
        return policy.replace("{inline}", hashes)

    def _wants_no_store(self, path: str, headers: Headers) -> bool:
        if path == AUTH_PATH_PREFIX or path.startswith(AUTH_PATH_PREFIX + "/"):
            return True
        if path in ADMIN_AUTH_PATHS:
            return True
        if "authorization" in headers:
            return True
        cookies = {
            part.split("=", 1)[0].strip()
            for part in headers.get("cookie", "").split(";")
        }
        return self.admin_cookie_name in cookies

    @staticmethod
    def _declared_length(headers: Headers) -> int:
        try:
            return int(headers.get("content-length", "0"))
        except ValueError:
            # Malformed: the server or the app rejects it; byte counting
            # still applies.
            return 0

    @staticmethod
    async def _internal_error(send: Send) -> None:
        body = json.dumps({"detail": INTERNAL_ERROR_DETAIL}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _too_large(send: Send) -> None:
        body = json.dumps({"detail": TOO_LARGE_DETAIL}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

"""Child process for the panel HTTP tests (run by ``panel_process``).

fastadmin reads its environment once, when it is imported, so the mounted
panel lives in a clean interpreter. It serves a line protocol on stdin and
stdout: one JSON request per line in, one JSON response per line out. Each
named client keeps its own cookie jar, so a test can be several users at
once. Nothing but protocol lines may be written to stdout.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

from httpx import ASGITransport, AsyncClient

from api.app import create_app


def _configure_logging() -> None:
    """DEBUG logs to stderr for the log-leak test. SQLAlchemy's own echo
    logging prints bound parameters by design and is an explicit operator
    opt-in the application never makes, so it stays at its default."""
    if not os.environ.get("PANEL_DEBUG_LOGS"):
        return
    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, force=True)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    # The in-process test client logs every request URL, query string
    # included; a real server's access log does that too, outside our code.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("panel.canary").debug("canary")


def _simulate_unbound_user() -> None:
    """Make fastadmin's second user lookup (the one that binds the user to
    the model admins) come back empty, as if the user were gone between its
    two ``get_obj`` calls. The first lookup, which validates the session,
    still runs."""
    if not os.environ.get("PANEL_SIMULATE_UNBOUND_USER"):
        return
    from fastadmin.api.service import ApiService, get_user_id_from_session_id

    async def authenticated(
        self: Any, session_id: str | None
    ) -> tuple[Any, None]:
        user_id = await get_user_id_from_session_id(session_id)
        if not user_id:
            from fastadmin import AdminApiException

            raise AdminApiException(401, detail="User is not authenticated.")
        return user_id, None

    ApiService._get_authenticated_user = authenticated


async def _serve() -> None:
    _configure_logging()
    app = create_app()
    _simulate_unbound_user()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    clients: dict[str, AsyncClient] = {}
    loop = asyncio.get_running_loop()

    async def handle(command: dict[str, Any]) -> dict[str, Any]:
        client = clients.setdefault(
            command["client"],
            AsyncClient(transport=transport, base_url="http://test"),
        )
        response = await client.request(
            command["method"],
            command["path"],
            params=command.get("params"),
            json=command.get("json"),
            headers=command.get("headers"),
            files={
                name: (filename, content.encode())
                for name, (filename, content) in (
                    command.get("files") or {}
                ).items()
            }
            or None,
        )
        return {
            "status": response.status_code,
            "text": response.text,
            "cookies": sorted(client.cookies.keys()),
            "set_cookie": response.headers.get_list("set-cookie"),
        }

    while line := await loop.run_in_executor(None, sys.stdin.readline):
        # A task per request mirrors the server (one task per request) and
        # keeps fastadmin's per-request context variables from leaking
        # from one request into the next.
        result = await asyncio.create_task(handle(json.loads(line)))
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()
    for client in clients.values():
        await client.aclose()
    from components.core.database import get_engine

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(_serve())

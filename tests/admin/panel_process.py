"""Drive the mounted admin panel over HTTP from a test.

The panel runs in a child interpreter (see ``panel_child``) against the
test database; the test talks to it through ``Panel.request``.
"""

import json
import os
import select
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from conftest import TEST_JWT_SECRET

ROOT = Path(__file__).resolve().parents[2]
ADMIN_SECRET = "admin-secret-for-tests-0123456789abcdef-xyz"
API = "/admin/api"
# A hung child fails the test instead of hanging the suite.
REQUEST_TIMEOUT = 60.0


@dataclass
class Reply:
    status: int
    text: str
    cookies: list[str]
    set_cookie: list[str]

    def json(self) -> Any:
        return json.loads(self.text)


def child_environment() -> dict[str, str]:
    """Environment of a process serving the app with the panel mounted."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ADMIN_", "RABBITMQ_", "JWT_"))
    }
    env.update(
        JWT_SECRET_KEY=TEST_JWT_SECRET,
        ADMIN_SECRET_KEY=ADMIN_SECRET,
        ADMIN_SESSION_COOKIE_SECURE="false",
        # Never connected to; required by the settings schema.
        RABBITMQ_URL="amqp://unused/",
        PYTHONPATH=str(ROOT),
    )
    return env


class Panel:
    """A running child process serving the app with the panel mounted.

    With ``debug_logs`` the child logs at DEBUG to stderr; ``close()``
    returns everything it wrote there."""

    def __init__(
        self,
        *,
        debug_logs: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        env = child_environment()
        env.update(extra_env or {})
        if debug_logs:
            env["PANEL_DEBUG_LOGS"] = "1"
        self._stderr: IO[bytes] = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            [sys.executable, "-m", "tests.admin.panel_child"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
        )

    def request(
        self,
        client: str,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: dict[str, tuple[str, str]] | None = None,
    ) -> Reply:
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        command = {
            "client": client,
            "method": method,
            "path": path,
            "json": json_body,
            "params": params,
            "headers": headers,
            "files": files,
        }
        self._process.stdin.write(json.dumps(command) + "\n")
        self._process.stdin.flush()
        ready, _, _ = select.select(
            [self._process.stdout], [], [], REQUEST_TIMEOUT
        )
        if not ready:
            self._process.kill()
            raise AssertionError(
                f"panel process gave no answer in {REQUEST_TIMEOUT}s for "
                f"{method} {path}"
            )
        line = self._process.stdout.readline()
        if not line:
            self._stderr.seek(0)
            raise AssertionError(
                "panel process died: " + self._stderr.read()[-2000:].decode()
            )
        data = json.loads(line)
        return Reply(
            data["status"], data["text"], data["cookies"], data["set_cookie"]
        )

    def sign_in(self, client: str, email: str, password: str) -> Reply:
        return self.request(
            client,
            "POST",
            f"{API}/sign-in",
            json_body={"username": email, "password": password},
        )

    def get(self, client: str, path: str, **params: Any) -> Reply:
        return self.request(client, "GET", f"{API}{path}", params=params)

    def close(self) -> str:
        """Stop the child; returns what it wrote to stderr."""
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        if self._process.stdout is not None:
            self._process.stdout.close()
        self._stderr.seek(0)
        logs = self._stderr.read().decode(errors="replace")
        self._stderr.close()
        return logs

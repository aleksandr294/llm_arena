"""Exit-code and cleanup semantics of scripts/sqlmap_scan.sh.

sqlmap itself is replaced by a fake executable that prints canned output, and
the target is a stdlib HTTP server on localhost, so nothing here needs network
access, Docker or the real tool. The cases mirror what the CI job relies on:
a clean scan passes, a confirmed injection, an incomplete run, a baseline
server error and an empty plan fail, an unreachable parameter is listed but
does not fail, and sqlmap session files never survive a run.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sqlmap_scan.sh"
TOKEN = "super-token-value"
ADMIN_TOKEN = "admin-token-value"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("timeout") is None,
    reason="needs bash and coreutils timeout",
)

CLEAN = (
    "[00:00:01] [WARNING] GET parameter 'q' does not seem to be injectable\n"
    "[00:00:01] [CRITICAL] all tested parameters do not appear to be "
    "injectable.\n\n[*] ending @ 00:00:02 /2026-11-06/\n"
)
INJECTION = (
    "sqlmap identified the following injection point(s) with a total of 9 "
    "HTTP(s) requests:\n---\nParameter: q (GET)\n"
    "    Type: boolean-based blind\n---\n\n[*] ending @ 00:00:02 /x/\n"
)
CRITICAL = (
    "[00:00:01] [CRITICAL] unable to connect to the target URL "
    "('Connection refused')\n\n[*] ending @ 00:00:02 /x/\n"
)

FAKE = f"""#!{sys.executable}
import os, sys, time
args = sys.argv[1:]
out = args[args.index("--output-dir") + 1]
os.makedirs(out + "/host", exist_ok=True)
for name in ("target.txt", "session.sqlite", "session.sqlite-wal"):
    with open(out + "/host/" + name, "w") as handle:
        handle.write(" ".join(args))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(args) + "\\n")
mode = os.environ["FAKE_MODE"]
if mode == "hang":
    time.sleep(60)
sys.stdout.write({{"clean": {CLEAN!r}, "inject": {INJECTION!r},
                   "critical": {CRITICAL!r}}}[mode])
"""


class _Target(BaseHTTPRequestHandler):
    def _answer(self) -> None:
        size = int(self.headers.get("Content-Length", 0))
        self.rfile.read(size)
        auth = self.headers.get("Authorization", "")
        if self.path.startswith("/boom"):
            code = 500
        elif self.path.startswith("/denied"):
            code = 403
        elif self.path.startswith("/admin-only"):
            code = 200 if auth == f"Bearer {ADMIN_TOKEN}" else 403
        elif self.path.startswith("/private"):
            code = 200 if auth else 401
        elif self.path.startswith("/login"):
            code = 401
        else:
            code = 200
        body = b"{}"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture(scope="module")
def target() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _doc(*paths: str, secured: bool = False) -> dict[str, Any]:
    param = {"name": "q", "in": "query", "schema": {"type": "string"}}
    op: dict[str, Any] = {"parameters": [param]}
    if secured:
        op["security"] = [{"HTTPBearer": []}]
    return {"paths": {path: {"get": dict(op)} for path in paths}}


def _run(
    tmp_path: Path,
    doc: dict[str, Any],
    host: str,
    mode: str,
    **env: str,
) -> tuple[int, Path, list[str]]:
    fake = tmp_path / "fake-sqlmap"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    openapi = tmp_path / "openapi.json"
    openapi.write_text(json.dumps(doc))
    reports = tmp_path / "reports"
    reports.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("")
    environment = {
        "PATH": os.environ["PATH"],
        "SQLMAP_CMD": str(fake),
        "PYTHON": sys.executable,
        "FAKE_MODE": mode,
        "FAKE_LOG": str(log),
        "SQLMAP_PARALLEL": "2",
        **env,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), str(openapi), host, str(reports)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return result.returncode, reports, log.read_text().splitlines()


def _no_session_files(reports: Path) -> bool:
    names = {"target.txt", "session.sqlite", "session.sqlite-wal"}
    return not any(p.name in names for p in reports.rglob("*"))


def test_clean_scan_passes_and_reports_coverage(
    tmp_path: Path, target: str
) -> None:
    code, reports, calls = _run(tmp_path, _doc("/ok"), target, "clean")
    assert code == 0
    assert len(calls) == 1 and "-p q" in calls[0]
    assert (reports / "findings.txt").read_text() == ""
    assert (reports / "scan_failures.txt").read_text() == ""
    first = (reports / "reached.txt").read_text().splitlines()[0]
    assert first.startswith("reached 1 / not reached 0 of 1 scannable")
    assert _no_session_files(reports)


def test_confirmed_injection_fails_and_names_the_parameter(
    tmp_path: Path, target: str
) -> None:
    code, reports, _ = _run(tmp_path, _doc("/ok"), target, "inject")
    assert code == 1
    text = (reports / "findings.txt").read_text()
    assert "INJECTION GET /ok parameter=q" in text
    assert "boolean-based blind" in text
    assert _no_session_files(reports)


def test_critical_error_is_an_incomplete_scan(
    tmp_path: Path, target: str
) -> None:
    code, reports, _ = _run(tmp_path, _doc("/ok"), target, "critical")
    assert code == 2
    assert "INCOMPLETE GET /ok" in (reports / "scan_failures.txt").read_text()


def test_baseline_server_error_fails_without_running_sqlmap(
    tmp_path: Path, target: str
) -> None:
    code, reports, calls = _run(tmp_path, _doc("/boom"), target, "clean")
    assert code == 2 and calls == []
    assert (
        "baseline request failed (status 500)"
        in (reports / "scan_failures.txt").read_text()
    )


def test_connection_failure_fails(tmp_path: Path) -> None:
    code, reports, calls = _run(tmp_path, _doc("/ok"), "127.0.0.1:9", "clean")
    assert code == 2 and calls == []
    assert "no-answer" in (reports / "scan_failures.txt").read_text()


def test_unreachable_parameter_is_listed_not_failed(
    tmp_path: Path, target: str
) -> None:
    doc = _doc("/denied", "/ok")
    code, reports, calls = _run(tmp_path, doc, target, "clean")
    assert code == 0
    assert len(calls) == 1  # only /ok reached sqlmap
    lines = (reports / "reached.txt").read_text().splitlines()
    assert lines[0].startswith("reached 1 / not reached 1 of 2")
    assert any(x.startswith("NOT_REACHED status=403") for x in lines)
    plan = json.loads((reports / "plan.json").read_text())
    missed = [
        p
        for op in plan["operations"]
        for p in op["parameters"]
        if p["status"] == "not_reached"
    ]
    assert missed and "status 403" in missed[0]["reason"]


def test_public_sign_in_401_counts_as_reached(
    tmp_path: Path, target: str
) -> None:
    code, _, calls = _run(tmp_path, _doc("/login"), target, "clean")
    assert code == 0 and len(calls) == 1
    assert "--ignore-code 401" in calls[0]


def test_the_token_that_works_is_chosen_per_operation(
    tmp_path: Path, target: str
) -> None:
    doc = _doc("/admin-only", secured=True)
    env = {"SCAN_TOKEN": TOKEN, "SCAN_ADMIN_TOKEN": ADMIN_TOKEN}
    code, reports, calls = _run(tmp_path, doc, target, "clean", **env)
    assert code == 0 and len(calls) == 1
    assert f"Bearer {ADMIN_TOKEN}" in calls[0] and TOKEN not in calls[0]
    assert "token=admin" in (reports / "reached.txt").read_text()


def test_authenticated_operation_without_a_valid_token_is_not_reached(
    tmp_path: Path, target: str
) -> None:
    env = {"SCAN_TOKEN": TOKEN}
    doc = _doc("/admin-only", secured=True)
    code, reports, calls = _run(tmp_path, doc, target, "clean", **env)
    assert code == 0 and calls == []
    assert "NOT_REACHED" in (reports / "reached.txt").read_text()


def test_empty_plan_fails(tmp_path: Path, target: str) -> None:
    code, reports, calls = _run(tmp_path, {"paths": {}}, target, "clean")
    assert code == 2 and calls == []
    assert "no parameter to scan" in (reports / "scan_failures.txt").read_text()


def test_killed_run_is_incomplete_and_leaves_no_session_files(
    tmp_path: Path, target: str
) -> None:
    code, reports, _ = _run(
        tmp_path, _doc("/ok"), target, "hang", SQLMAP_JOB_LIMIT="1"
    )
    assert code == 2
    assert "INCOMPLETE GET /ok" in (reports / "scan_failures.txt").read_text()
    assert _no_session_files(reports)

"""Scan plan generation and sqlmap output parsing for the CI scan."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.security.helpers import CORPUS, MARK, huge_cases

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sqlmap_plan.py"
UUID = "00000000-0000-4000-8000-000000000001"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("sqlmap_plan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sqlmap_plan"] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> str:
    # read_text would translate the CRLF line endings of the request.
    return path.read_bytes().decode()


plan_mod = _load()

DOC: dict[str, Any] = {
    "openapi": "3.1.0",
    "paths": {
        "/health": {"get": {"responses": {}}},
        "/items/{item_id}": {
            "get": {
                "security": [{"HTTPBearer": []}],
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "schema": {"type": "string", "format": "uuid"},
                    },
                    {
                        "name": "q",
                        "in": "query",
                        "schema": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer", "minimum": 1},
                    },
                    {
                        "name": "flag",
                        "in": "query",
                        "schema": {"type": "boolean"},
                    },
                    {
                        "name": "since",
                        "in": "query",
                        "schema": {"type": "string", "format": "date-time"},
                    },
                    {
                        "name": "status",
                        "in": "query",
                        "schema": {"$ref": "#/components/schemas/Status"},
                    },
                ],
            }
        },
        "/items": {
            "post": {
                "security": [{"HTTPBearer": []}],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Body"}
                        }
                    }
                },
            }
        },
    },
    "components": {
        "schemas": {
            "Status": {"type": "string", "enum": ["open", "closed"]},
            "Body": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 3},
                    "count": {"type": "integer"},
                    "owner_id": {"type": "string", "format": "uuid"},
                    "status": {"$ref": "#/components/schemas/Status"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "nested": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/Inner"},
                            {"type": "null"},
                        ]
                    },
                },
            },
            "Inner": {
                "type": "object",
                "properties": {"note": {"type": "string"}},
            },
        }
    },
}


def _status(op: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {p["path"]: (p["status"], p["reason"]) for p in op["parameters"]}


def _op(plan: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    for op in plan["operations"]:
        if op["method"] == method and op["path"] == path:
            return op  # type: ignore[no-any-return]
    raise AssertionError(f"{method} {path} not in plan")


def test_query_and_path_parameters(tmp_path: Path) -> None:
    plan = plan_mod.generate_plan(DOC, tmp_path)
    op = _op(plan, "GET", "/items/{item_id}")
    params = _status(op)
    assert params["q"][0] == "scanned"
    for name in ("item_id", "limit", "flag", "since", "status"):
        assert params[name][0] == "skipped"
        assert params[name][1]
    assert op["test_parameters"] == ["q"]
    request = _read(tmp_path / op["request_file"])
    assert request.startswith(f"GET /items/{UUID}?")
    assert "q=x" in request and "limit=1" in request
    assert "flag=true" in request and "status=open" in request
    assert "since=2024-01-01T00%3A00%3A00Z" in request
    assert "Host: api:8000\r\n" in request


def test_json_body_is_built_from_schema(tmp_path: Path) -> None:
    plan = plan_mod.generate_plan(DOC, tmp_path, host="h:1")
    op = _op(plan, "POST", "/items")
    text = _read(tmp_path / op["request_file"])
    head, body = text.split("\r\n\r\n", 1)
    assert "Content-Type: application/json" in head
    assert "Host: h:1" in head
    assert json.loads(body) == {
        "name": "xxx",
        "count": 1,
        "owner_id": UUID,
        "status": "open",
        "tags": ["x"],
        "nested": {"note": "x"},
    }
    params = _status(op)
    assert params["name"][0] == "scanned"
    assert params["tags[]"][0] == "scanned"
    assert params["nested.note"][0] == "scanned"
    for name in ("count", "owner_id", "status"):
        assert params[name][0] == "skipped"
    assert op["test_parameters"] == ["name", "note", "tags"]


def test_public_and_authenticated_are_flagged(tmp_path: Path) -> None:
    plan = plan_mod.generate_plan(DOC, tmp_path)
    assert _op(plan, "GET", "/health")["authenticated"] is False
    assert _op(plan, "GET", "/health")["scan"] is False
    assert _op(plan, "POST", "/items")["authenticated"] is True
    assert "Authorization" not in "".join(
        p.read_text() for p in tmp_path.glob("*.req")
    )


def test_plan_json_is_written(tmp_path: Path) -> None:
    plan = plan_mod.generate_plan(DOC, tmp_path)
    assert json.loads((tmp_path / "plan.json").read_text()) == plan
    assert len(list(tmp_path.glob("*.req"))) == len(plan["operations"]) == 3


def test_new_operation_is_covered_without_changes(tmp_path: Path) -> None:
    doc = json.loads(json.dumps(DOC))
    doc["paths"]["/search"] = {
        "get": {
            "parameters": [
                {"name": "term", "in": "query", "schema": {"type": "string"}}
            ]
        }
    }
    plan = plan_mod.generate_plan(doc, tmp_path)
    op = _op(plan, "GET", "/search")
    assert op["scan"] is True
    assert op["test_parameters"] == ["term"]
    assert (
        (tmp_path / op["request_file"])
        .read_text()
        .startswith("GET /search?term=x ")
    )


def test_free_string_path_parameter_is_marked(tmp_path: Path) -> None:
    doc = {
        "paths": {
            "/n/{name}": {
                "get": {
                    "parameters": [
                        {
                            "name": "name",
                            "in": "path",
                            "schema": {"type": "string"},
                        }
                    ]
                }
            }
        }
    }
    plan = plan_mod.generate_plan(doc, tmp_path)
    op = plan["operations"][0]
    assert op["scan"] is True
    assert _read(tmp_path / op["request_file"]).startswith("GET /n/x* ")


def test_cli_plan_and_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "openapi.json"
    src.write_text(json.dumps(DOC))
    assert plan_mod.main(["plan", str(src), str(tmp_path / "out")]) == 0
    assert "3 operations" in capsys.readouterr().out
    log = tmp_path / "o.txt"
    log.write_text(CLEAN_OK)
    assert plan_mod.main(["findings", str(log), "GET /a"]) == 0
    log.write_text(INJECTABLE)
    assert plan_mod.main(["findings", str(log), "GET /a"]) == 1
    assert "parameter=q" in capsys.readouterr().out
    log.write_text(TRUNCATED)
    assert plan_mod.main(["findings", str(log), "GET /a"]) == 2


def test_real_app_schema_is_planned(tmp_path: Path) -> None:
    env = {
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/x_test",
        "RABBITMQ_URL": "amqp://u:p@localhost:5672//",
        "JWT_SECRET_KEY": "k" * 40,
    }
    for key, value in env.items():
        os.environ.setdefault(key, value)
    from api.app import create_app
    from tests.helpers import make_settings

    settings = make_settings(
        database_url=env["DATABASE_URL"],
        rabbitmq_url=env["RABBITMQ_URL"],
        jwt_secret_key="k" * 40,
    )
    doc = create_app(settings).openapi()
    plan = plan_mod.generate_plan(doc, tmp_path)
    ops = plan["operations"]
    assert len(ops) == sum(
        1 for item in doc["paths"].values() for m in item if m != "parameters"
    )
    assert _op(plan, "POST", "/api/v1/auth/login")["authenticated"] is False
    assert _op(plan, "POST", "/api/v1/datasets")["authenticated"] is True
    for op in ops:
        for param in op["parameters"]:
            assert param["status"] in ("scanned", "skipped")
            assert param["reason"]
    evaluations = _op(
        plan, "GET", "/api/v1/experiments/{experiment_id}/evaluations"
    )
    assert evaluations["test_parameters"] == ["evaluator_id"]
    assert any(op["scan"] and op["method"] == "POST" for op in ops)


# Samples below follow sqlmap 1.10.11 output captured from real runs; a clean
# run ends with a CRITICAL verdict line, which is not an error.
CLEAN = """\
[00:41:49] [WARNING] heuristic (basic) test shows that GET parameter 'q' \
might not be SQL injectable
[00:41:51] [WARNING] GET parameter 'q' does not seem to be injectable
[00:41:51] [CRITICAL] all tested parameters do not appear to be injectable. \
Try to increase values for '--level'/'--risk' options if you wish to perform \
more tests.

[*] ending @ 00:41:51 /2026-11-06/
"""

CLEAN_OK = CLEAN

UNREACHABLE = """\
[00:41:51] [INFO] testing connection to the target URL
[00:41:51] [CRITICAL] unable to connect to the target URL \
('Connection refused'). sqlmap is going to retry the request(s)
[00:41:51] [CRITICAL] unable to connect to the target URL \
('Connection refused')

[*] ending @ 00:41:51 /2026-11-06/
"""

NO_PARAMS = """\
[00:41:52] [CRITICAL] specified file 'nop.req' does not contain a usable \
HTTP request (with parameters)

[*] ending @ 00:41:52 /2026-11-06/
"""

ABSENT_PARAM = """\
[00:41:52] [CRITICAL] all testable parameters you provided are not present \
within the given request data

[*] ending @ 00:41:52 /2026-11-06/
"""

JSON_INJECTABLE = """\
(custom) POST parameter 'JSON name' is vulnerable. Do you want to keep \
testing the others (if any)? [y/N] N
sqlmap identified the following injection point(s) with a total of 126 \
HTTP(s) requests:
---
Parameter: JSON name ((custom) POST)
    Type: boolean-based blind
    Title: AND boolean-based blind - WHERE or HAVING clause
    Payload: {"name":"alpha' AND 5741=5741 AND 'NPQa'='NPQa","n":1}
---
[00:41:35] [WARNING] the back-end DBMS is not PostgreSQL
[00:41:35] [CRITICAL] sqlmap was not able to fingerprint the back-end \
database management system

[*] ending @ 00:41:35 /2026-11-06/
"""

INJECTABLE = """\
[12:00:01] [INFO] GET parameter 'q' is 'AND boolean-based blind' injectable
sqlmap identified the following injection point(s) with a total of 46 \
HTTP(s) requests:
---
Parameter: q (GET)
    Type: boolean-based blind
    Title: AND boolean-based blind - WHERE or HAVING clause
    Payload: q=x' AND 1=1-- a

    Type: time-based blind
    Title: PostgreSQL > 8.1 AND time-based blind
    Payload: q=x' AND 1=(SELECT 1 FROM PG_SLEEP(5))-- a
---
[12:00:09] [INFO] the back-end DBMS is PostgreSQL
[*] ending @ 12:00:10 /2026-11-06/
"""

HEURISTIC = """\
[12:00:01] [WARNING] heuristic (basic) test shows that GET parameter 'q' \
might be injectable (possible DBMS: 'PostgreSQL')
[12:00:05] [WARNING] GET parameter 'q' does not seem to be injectable
[12:00:05] [CRITICAL] all tested parameters do not appear to be injectable.

[*] ending @ 12:00:06 /2026-11-06/
"""

# Non-fatal CRITICAL lines seen against the real API: the run still completes.
NOISY_CLEAN = """\
[22:57:14] [CRITICAL] heuristics detected that the target is protected by \
some kind of WAF/IPS
[22:57:14] [CRITICAL] target URL content appears to be heavily dynamic. \
sqlmap is going to retry the request(s)
[22:57:18] [WARNING] (custom) POST parameter 'JSON name' does not seem to be \
injectable
[22:57:18] [CRITICAL] all tested parameters do not appear to be injectable.

[*] ending @ 22:57:18 /2026-11-05/
"""

NOT_AUTHORIZED = """\
[22:57:12] [CRITICAL] not authorized, try to provide right HTTP \
authentication type and valid credentials (401).
[22:57:12] [WARNING] HTTP error codes detected during run:

[*] ending @ 22:57:12 /2026-11-05/
"""

NOT_FOUND = """\
[22:57:17] [CRITICAL] page not found (404)

[*] ending @ 22:57:17 /2026-11-05/
"""

TRUNCATED = """\
[12:00:00] [INFO] testing connection to the target URL
[12:00:01] [INFO] testing 'AND boolean-based blind - WHERE or HAVING clause'
"""


def test_clean_run_has_no_findings() -> None:
    assert plan_mod.parse_findings(CLEAN_OK, "GET /a") == []
    assert plan_mod.scan_completed(CLEAN_OK) is True


def test_confirmed_injection_is_reported() -> None:
    found = plan_mod.parse_findings(INJECTABLE, "GET /a")
    assert [(f.endpoint, f.parameter, f.place, f.technique) for f in found] == [
        ("GET /a", "q", "GET", "boolean-based blind"),
        ("GET /a", "q", "GET", "time-based blind"),
    ]
    assert plan_mod.scan_completed(INJECTABLE) is True


def test_heuristic_note_is_not_a_finding() -> None:
    assert plan_mod.parse_findings(HEURISTIC, "GET /a") == []
    assert plan_mod.scan_completed(HEURISTIC) is True


def test_noncritical_critical_lines_do_not_fail_a_clean_run() -> None:
    assert plan_mod.parse_findings(NOISY_CLEAN, "POST /o") == []
    assert plan_mod.scan_completed(NOISY_CLEAN) is True


def test_json_body_injection_place() -> None:
    found = plan_mod.parse_findings(JSON_INJECTABLE, "POST /items")
    assert [(f.parameter, f.place, f.technique) for f in found] == [
        ("JSON name", "(custom) POST", "boolean-based blind")
    ]


@pytest.mark.parametrize("output", ["", TRUNCATED, "garbage \x00 text"])
def test_unparseable_output_is_incomplete(output: str) -> None:
    assert plan_mod.parse_findings(output, "GET /a") == []
    assert plan_mod.scan_completed(output) is False


@pytest.mark.parametrize(
    "output",
    [UNREACHABLE, NO_PARAMS, ABSENT_PARAM, NOT_AUTHORIZED, NOT_FOUND],
    ids=["dead", "none", "-p", "401", "404"],
)
def test_critical_error_is_incomplete(output: str) -> None:
    assert plan_mod.parse_findings(output, "GET /a") == []
    assert plan_mod.scan_completed(output) is False


def test_operation_with_marker_and_named_parameter_is_two_jobs(
    tmp_path: Path,
) -> None:
    doc = {
        "paths": {
            "/n/{name}": {
                "get": {
                    "parameters": [
                        {
                            "name": "name",
                            "in": "path",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                    ]
                }
            }
        }
    }
    plan = plan_mod.generate_plan(doc, tmp_path)
    jobs = plan["operations"][0]["jobs"]
    assert [j["test_parameters"] for j in jobs] == [["q"], []]
    assert "*" not in _read(tmp_path / jobs[0]["file"]).split("\r\n")[0]
    assert "*" in _read(tmp_path / jobs[1]["file"]).split("\r\n")[0]
    assert plan_mod.main(["jobs", str(tmp_path)]) == 0


def test_values_fill_seeded_ids_into_the_request(
    tmp_path: Path,
) -> None:
    doc = {
        "paths": {
            "/o/{organization_id}": {
                "patch": {
                    "security": [{"HTTPBearer": []}],
                    "parameters": [
                        {
                            "name": "organization_id",
                            "in": "path",
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        }
                    },
                }
            },
            "/e/{experiment_id}": {
                "get": {
                    "parameters": [
                        {
                            "name": "experiment_id",
                            "in": "path",
                            "schema": {"type": "string", "format": "uuid"},
                        },
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                    ]
                }
            },
        }
    }
    values = {"organization_id": "11111111-1111-4111-8111-111111111111"}
    plan = plan_mod.generate_plan(doc, tmp_path, values=values)
    by_path = {op["path"]: op for op in plan["operations"]}
    org = _read(tmp_path / by_path["/o/{organization_id}"]["request_file"])
    assert org.startswith(f"PATCH /o/{values['organization_id']} ")
    experiment = _read(tmp_path / by_path["/e/{experiment_id}"]["request_file"])
    assert experiment.startswith(f"GET /e/{UUID}?")


def test_jobs_ignore_only_the_codes_the_operation_can_return(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_mod.generate_plan(DOC, tmp_path)
    assert plan_mod.main(["jobs", str(tmp_path)]) == 0
    lines = [line.split("\t") for line in capsys.readouterr().out.splitlines()]
    assert lines and all(len(line) == 6 for line in lines)
    for _label, _file, auth, params, ignore, _endpoint in lines:
        assert params != ""
        assert ("401" in ignore.split(",")) == (auth == "0")


def _body_doc() -> dict[str, Any]:
    body = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "dataset_id": {"type": "string", "format": "uuid"},
                        "choice": {
                            "anyOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {"deep": {"type": "string"}},
                                },
                            ]
                        },
                    },
                }
            }
        }
    }
    return {"paths": {"/x/{item_id}": {"post": {"requestBody": body}}}}


def test_values_reach_body_fields_and_scoped_keys_win(tmp_path: Path) -> None:
    values = {
        "dataset_id": "22222222-2222-4222-8222-222222222222",
        "name": "plain",
        "POST /x/{item_id}:name": "scoped",
    }
    plan = plan_mod.generate_plan(_body_doc(), tmp_path, values=values)
    request = _read(tmp_path / plan["operations"][0]["request_file"])
    body = json.loads(request.split("\r\n\r\n", 1)[1])
    assert body["dataset_id"] == values["dataset_id"]
    assert body["name"] == "scoped"


def test_other_anyof_alternatives_are_listed_as_skipped(
    tmp_path: Path,
) -> None:
    plan = plan_mod.generate_plan(_body_doc(), tmp_path)
    entries = plan["operations"][0]["parameters"]
    alternatives = [e for e in entries if "alternative" in e["path"]]
    assert alternatives
    assert all(e["status"] == "skipped" and e["reason"] for e in alternatives)
    assert any(e["name"] == "deep" for e in alternatives)


@pytest.mark.parametrize(
    ("status", "public", "expected"),
    [
        (200, False, "reached"),
        (201, True, "reached"),
        (401, True, "reached"),
        (401, False, "not_reached"),
        (403, False, "not_reached"),
        (404, True, "not_reached"),
        (422, False, "not_reached"),
        (500, False, "failed"),
        (503, True, "failed"),
        (None, True, "failed"),
    ],
)
def test_baseline_classification(
    status: int | None, public: bool, expected: str
) -> None:
    assert plan_mod.classify_baseline(status, public) == expected


def test_coverage_counts_reached_and_not_reached(tmp_path: Path) -> None:
    plan_mod.generate_plan(DOC, tmp_path / "plan")
    plan = json.loads((tmp_path / "plan" / "plan.json").read_text())
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    jobs = [
        (op["id"], len(op["jobs"])) for op in plan["operations"] if op["jobs"]
    ]
    assert len(jobs) >= 2
    for index, (op_id, count) in enumerate(jobs):
        for n in range(count):
            directory = tmp_path / "sqlmap" / f"{op_id}.{n}"
            directory.mkdir(parents=True)
            line = "reached 200 admin" if index else "not_reached 403 admin"
            (directory / "baseline").write_text(line)
    merged, lines, summary = plan_mod.build_coverage(tmp_path)
    assert summary.startswith("reached ")
    assert "not reached " in summary
    assert any(line.startswith("NOT_REACHED status=403") for line in lines)
    states = {
        p["status"]
        for op in merged["operations"]
        for p in op["parameters"]
        if p["name"] == "q" or p["status"] != "skipped"
    }
    assert "not_reached" in states and "scanned" in states
    missed = [
        p
        for op in merged["operations"]
        for p in op["parameters"]
        if p["status"] == "not_reached"
    ]
    assert all("status 403" in p["reason"] for p in missed)


@pytest.mark.parametrize(
    "text", ["", "garbage", "reached", "\x00\x00 \x00", "weird 200 admin"]
)
def test_unreadable_baseline_fails_closed(tmp_path: Path, text: str) -> None:
    path = tmp_path / "baseline"
    path.write_text(text)
    verdict, status, _ = plan_mod._read_baseline(path)
    assert verdict == "not_reached" and status == "unknown"
    assert plan_mod._read_baseline(tmp_path / "missing")[0] == "not_reached"


# --- hostile input: sqlmap output and OpenAPI names are untrusted text ---

SPOOF_BLOCK = """\
Parameter: spoofed (GET)
    Type: boolean-based blind
[*] ending @ 00:00:00 /2026-11-06/
"""


@pytest.mark.parametrize("sample", list(CORPUS.values()), ids=list(CORPUS))
def test_reflected_text_does_not_change_the_verdict(sample: str) -> None:
    for log in (CLEAN, INJECTABLE):
        noisy = log.replace(
            "[WARNING]", f"[WARNING] reflected {sample!r} [WARNING]", 1
        )
        assert noisy.count(MARK) <= 1
        before = plan_mod.parse_findings(log, "GET /a")
        assert plan_mod.parse_findings(noisy, "GET /a") == before
        assert plan_mod.scan_completed(noisy) == plan_mod.scan_completed(log)


@pytest.mark.parametrize("sample", list(CORPUS.values()), ids=list(CORPUS))
def test_reflected_lines_alone_are_not_a_clean_scan(sample: str) -> None:
    # A target that echoes marker-like lines cannot make a run complete
    # without the verdict, and a spoofed block needs the header line.
    assert plan_mod.parse_findings(SPOOF_BLOCK + sample, "GET /a") == []
    assert plan_mod.scan_completed(SPOOF_BLOCK + sample) is False


def test_huge_untrusted_output_is_handled_quickly() -> None:
    import time

    for name, build in huge_cases().items():
        if name not in ("megabytes", "megabytes_lines", "regex_bomb"):
            continue
        text = "Parameter: " + build() + " (GET)\n" + build()
        start = time.monotonic()
        assert plan_mod.parse_findings(text, "GET /a") == []
        assert plan_mod.scan_completed(text) is False
        assert time.monotonic() - start < 5, name


def test_hostile_names_keep_jobs_and_requests_well_formed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = "a\tb\r\nInjected: yes\n"
    doc = {
        "paths": {
            f"/p{hostile}/{{x}}": {
                "get": {
                    "parameters": [
                        {
                            "name": f"q{hostile}",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "x",
                            "in": "path",
                            "schema": {"type": "string"},
                        },
                    ]
                }
            }
        }
    }
    plan_mod.generate_plan(doc, tmp_path)
    assert plan_mod.main(["jobs", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and all(len(x.split("\t")) == 6 for x in lines)
    for request in tmp_path.glob("*.req"):
        head = _read(request).split("\r\n\r\n", 1)[0].split("\r\n")
        assert "Injected: yes" not in head
        assert len(head[0].split(" ")) == 3

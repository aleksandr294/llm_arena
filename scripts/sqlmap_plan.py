"""Scan plan and result parser for the sqlmap CI job (standard library only).

Usage:
    python scripts/sqlmap_plan.py plan <openapi.json> <out-dir> [--host HOST]
        [--values values.json]
    python scripts/sqlmap_plan.py baseline <request-file> [--token-env VAR]
        [--public]
    python scripts/sqlmap_plan.py coverage <reports-dir>
    python scripts/sqlmap_plan.py jobs <plan-dir>
    python scripts/sqlmap_plan.py findings <output-file> <endpoint>

``plan`` writes one raw HTTP request file per operation (sqlmap ``-r``
format) and ``plan.json`` listing every parameter as scanned or skipped with
the reason. Request files never carry a credential: ``plan.json`` flags the
operations that need one and the caller adds the header (``--headers``).

``findings`` exits 0 for a complete run with no confirmed injection, 1 when
an injection is confirmed and 2 when the output is not a complete run, so an
unparseable or truncated run is never mistaken for a clean one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import quote

FIXED_UUID = "00000000-0000-4000-8000-000000000001"
DEFAULT_HOST = "api:8000"
_METHODS = ("get", "put", "post", "delete", "patch", "options", "head")
_MAX_DEPTH = 8
_SAMPLES = {
    "uuid": FIXED_UUID,
    "date": "2024-01-01",
    "date-time": "2024-01-01T00:00:00Z",
    "time": "00:00:00",
    "email": "x@example.com",
    "uri": "http://example.com",
}

Json = dict[str, Any]


def _resolve(schema: Json, doc: Json) -> Json:
    """Follow ``$ref`` chains into ``components/schemas``."""
    seen: set[str] = set()
    while "$ref" in schema:
        ref = str(schema["$ref"])
        if ref in seen or not ref.startswith("#/"):
            return {}
        seen.add(ref)
        node: Any = doc
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        schema = node if isinstance(node, dict) else {}
    return schema


def _variants(schema: Json, doc: Json) -> list[Json]:
    """Non-null alternatives of an ``anyOf``/``oneOf``/``allOf`` schema."""
    out: list[Json] = []
    for key in ("anyOf", "oneOf", "allOf"):
        for item in schema.get(key, []):
            item = _resolve(item, doc)
            if item.get("type") != "null":
                out.append(item)
    return out


def _is_object(schema: Json) -> bool:
    return schema.get("type") == "object" or "properties" in schema


def classify(schema: Json, doc: Json, depth: int = 0) -> tuple[bool, str]:
    """Return ``(scanned, reason)`` for a scalar (non-object) schema.

    Only a free string can carry an injection payload through validation, so
    every other type, format or enum is skipped with the reason.
    """
    schema = _resolve(schema, doc)
    if depth > _MAX_DEPTH:
        return False, "schema nesting too deep"
    if "enum" in schema or "const" in schema:
        return False, "enum: only declared values pass validation"
    variants = _variants(schema, doc)
    if variants:
        results = [classify(v, doc, depth + 1) for v in variants]
        for scanned, reason in results:
            if scanned:
                return True, reason
        return results[0]
    kind = schema.get("type")
    if kind == "string":
        fmt = schema.get("format")
        if fmt:
            return False, f"string format {fmt}: validated before use"
        return True, "free string"
    if kind == "array":
        return classify(schema.get("items", {}), doc, depth + 1)
    if kind in ("integer", "number", "boolean"):
        return False, f"{kind}: validated before use"
    if _is_object(schema):
        return False, "object without declared string fields"
    return False, "no usable type in schema"


def build_value(
    schema: Json,
    doc: Json,
    depth: int = 0,
    values: dict[str, Any] | None = None,
) -> Any:
    """Build a value that passes validation of ``schema``.

    ``values`` maps field names to a value that replaces the generated one
    (ids of seeded objects, dummy credentials that pass validation).
    """
    schema = _resolve(schema, doc)
    if depth > _MAX_DEPTH:
        return None
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    variants = _variants(schema, doc)
    if variants:
        return build_value(variants[0], doc, depth + 1, values)
    kind = schema.get("type")
    if kind == "string":
        sample = _SAMPLES.get(str(schema.get("format")))
        if sample is not None:
            return sample
        size = max(1, int(schema.get("minLength", 1)))
        if "maxLength" in schema:
            size = min(size, max(1, int(schema["maxLength"])))
        return "x" * size
    if kind == "integer":
        return int(max(1, schema.get("minimum", 1)))
    if kind == "number":
        return max(1, schema.get("minimum", 1))
    if kind == "boolean":
        return True
    if kind == "array":
        items = schema.get("items", {})
        return [build_value(items, doc, depth + 1, values)]
    if _is_object(schema):
        overrides = values or {}
        return {
            name: (
                overrides[name]
                if name in overrides
                else build_value(sub, doc, depth + 1, values)
            )
            for name, sub in schema.get("properties", {}).items()
        }
    return "x"


@dataclass(frozen=True)
class Finding:
    endpoint: str
    parameter: str
    place: str
    technique: str


def _walk_body(
    schema: Json, doc: Json, path: str, depth: int = 0
) -> list[Json]:
    """List the leaf fields of a JSON body schema as parameter entries."""
    schema = _resolve(schema, doc)
    if depth <= _MAX_DEPTH and "enum" not in schema:
        variants = _variants(schema, doc)
        if variants:
            first = _walk_body(variants[0], doc, path, depth + 1)
            # Only the first alternative is generated and scanned; the rest
            # are listed so the artifact still names every parameter.
            for index, other in enumerate(variants[1:], start=2):
                label = f"{path or 'body'}|alternative {index}"
                first.extend(
                    {
                        **entry,
                        "path": f"{label}:{entry['path']}",
                        "status": "skipped",
                        "reason": "alternative of anyOf/oneOf: only the "
                        "first is generated",
                    }
                    for entry in _walk_body(other, doc, path, depth + 1)
                )
            return first
        if schema.get("type") == "array":
            return _walk_body(
                schema.get("items", {}), doc, f"{path}[]", depth + 1
            )
        if _is_object(schema) and schema.get("properties"):
            entries: list[Json] = []
            for name, sub in schema["properties"].items():
                sub_path = f"{path}.{name}" if path else name
                entries.extend(_walk_body(sub, doc, sub_path, depth + 1))
            return entries
    scanned, reason = classify(schema, doc)
    name = re.split(r"[.\[\]]+", path.rstrip("[]"))[-1] or "body"
    return [
        {
            "name": name,
            "in": "body",
            "path": path or "body",
            "status": "scanned" if scanned else "skipped",
            "reason": reason,
        }
    ]


def _slug(method: str, path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", f"{method} {path}".lower()).strip("_")


def _operation_authenticated(operation: Json, doc: Json) -> bool:
    security = operation.get("security", doc.get("security"))
    return bool(security)


def _merged_parameters(
    path_item: Json, operation: Json, doc: Json
) -> list[Json]:
    merged: dict[tuple[str, str], Json] = {}
    for source in (path_item.get("parameters", []), operation["parameters"]):
        for param in source:
            param = _resolve(param, doc)
            merged[(param["in"], param["name"])] = param
    return list(merged.values())


def _plan_operation(
    method: str,
    path: str,
    path_item: Json,
    operation: Json,
    doc: Json,
    values: dict[str, Any],
) -> tuple[Json, str | None]:
    """Return the plan entry for one operation and its JSON body, if any."""
    # "METHOD /path:name" keys apply to one operation only and win over the
    # plain name (for example a rename that must not collide with a create).
    scope = f"{method.upper()} {path}:"
    values = {
        **values,
        **{
            k[len(scope) :]: v for k, v in values.items() if k.startswith(scope)
        },
    }
    operation = {"parameters": [], **operation}
    entries: list[Json] = []
    url_path = path
    query: list[str] = []
    for param in _merged_parameters(path_item, operation, doc):
        place = param["in"]
        schema = param.get("schema", {})
        if place not in ("path", "query"):
            entries.append(
                {
                    "name": param["name"],
                    "in": place,
                    "path": param["name"],
                    "status": "skipped",
                    "reason": f"{place} parameter is not scanned",
                }
            )
            continue
        scanned, reason = classify(schema, doc)
        value = values.get(param["name"], build_value(schema, doc))
        text = str(value[0] if isinstance(value, list) else value)
        text = "true" if text == "True" else text
        if place == "path":
            # sqlmap tests a path segment only at an explicit `*` marker.
            marked = text + "*" if scanned else text
            url_path = url_path.replace("{" + param["name"] + "}", marked)
        else:
            query.append(f"{quote(param['name'])}={quote(text)}")
        entries.append(
            {
                "name": param["name"],
                "in": place,
                "path": param["name"],
                "status": "scanned" if scanned else "skipped",
                "reason": reason,
            }
        )
    body: str | None = None
    request_body = _resolve(operation.get("requestBody", {}), doc)
    content = request_body.get("content", {})
    if "application/json" in content:
        schema = content["application/json"].get("schema", {})
        body = json.dumps(
            build_value(schema, doc, values=values), separators=(",", ":")
        )
        entries.extend(_walk_body(schema, doc, ""))
    elif content:
        entries.append(
            {
                "name": "body",
                "in": "body",
                "path": "body",
                "status": "skipped",
                "reason": "unsupported content type "
                + ", ".join(sorted(content)),
            }
        )
    # Names come from the OpenAPI document, which is not trusted text: keep
    # whitespace and control characters out of the request line.
    url_path = quote(url_path, safe="/*:@-._~%")
    target = url_path + ("?" + "&".join(query) if query else "")
    scanned_names = sorted(
        {
            e["name"]
            for e in entries
            if e["status"] == "scanned" and e["in"] in ("query", "body")
        }
    )
    entry = {
        "id": _slug(method, path),
        "method": method.upper(),
        "path": path,
        "authenticated": _operation_authenticated(operation, doc),
        "scan": any(e["status"] == "scanned" for e in entries),
        "test_parameters": scanned_names,
        "parameters": entries,
        "_target": target,
    }
    return entry, body


def _write_request(
    path: Path,
    *,
    method: str,
    url: str,
    host: str,
    headers: list[str],
    body: str | None,
) -> None:
    head = [f"{method.upper()} {url} HTTP/1.1", f"Host: {host}", *headers]
    text = "\r\n".join(head) + "\r\n\r\n" + (body or "")
    path.write_bytes(text.encode("utf-8"))


def generate_plan(
    doc: Json,
    out_dir: Path,
    host: str = DEFAULT_HOST,
    values: dict[str, Any] | None = None,
) -> Json:
    """Write the request files and ``plan.json``; return the plan.

    ``values`` maps parameter and field names to values that replace the
    generated ones: ids of seeded objects and dummy credentials.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    operations: list[Json] = []
    for path, path_item in sorted(doc.get("paths", {}).items()):
        for method in _METHODS:
            if method not in path_item:
                continue
            entry, body = _plan_operation(
                method,
                path,
                path_item,
                path_item[method],
                doc,
                values or {},
            )
            target = entry.pop("_target")
            headers = ["Accept: application/json"]
            if body is not None:
                headers.append("Content-Type: application/json")

            write = partial(
                _write_request,
                method=method,
                host=host,
                headers=headers,
                body=body,
            )

            # An explicit `*` marker makes sqlmap test only the marked
            # spot and ignore -p, so an operation with both a marked path
            # segment and named parameters is scanned as two requests.
            marked = "*" in target
            named = entry["test_parameters"]
            jobs: list[Json] = []
            if marked and named:
                write(
                    out_dir / f"{entry['id']}.req", url=target.replace("*", "")
                )
                write(out_dir / f"{entry['id']}.path.req", url=target)
                jobs = [
                    {"file": f"{entry['id']}.req", "test_parameters": named},
                    {"file": f"{entry['id']}.path.req", "test_parameters": []},
                ]
            else:
                write(out_dir / f"{entry['id']}.req", url=target)
                if marked or named:
                    jobs = [
                        {"file": f"{entry['id']}.req", "test_parameters": named}
                    ]
            entry["request_file"] = f"{entry['id']}.req"
            entry["jobs"] = jobs
            operations.append(entry)
    plan: Json = {"operations": operations}
    (out_dir / "plan.json").write_text(
        json.dumps(plan, indent=2) + "\n", encoding="utf-8"
    )
    return plan


_MAX_LINE = 2000
_HEADER = "sqlmap identified the following injection point(s)"
_PARAM = re.compile(r"^Parameter: (.+?) \((.+)\)$")
_TYPE = re.compile(r"^Type: (.+)$")
_END = re.compile(r"^\[\*\] ending @", re.MULTILINE)
_CRITICAL = re.compile(r"\[CRITICAL\] (.*)")
_VERDICT = "all tested parameters do not appear to be injectable"
# CRITICAL lines sqlmap 1.10.11 logs during a run that still completes.
_BENIGN_CRITICAL = (
    _VERDICT,
    "heuristics detected that the target is protected by some kind of WAF",
    "target URL content appears to be heavily dynamic",
    "sqlmap is going to retry the request(s)",
)


def parse_findings(output: str, endpoint: str) -> list[Finding]:
    """Extract confirmed injections from sqlmap output.

    Only the block that follows the "identified the following injection
    point(s)" line counts. Heuristic notes ("might be injectable") are not
    findings. One finding per parameter and technique.
    """
    findings: list[Finding] = []
    in_block = False
    fences = 0
    parameter: tuple[str, str] | None = None
    for raw in output.splitlines():
        if len(raw) > _MAX_LINE:
            # Reflected target content; no sqlmap marker is this long, and
            # the parameter pattern must not run over megabytes.
            continue
        line = raw.strip()
        if _HEADER in line:
            in_block, fences, parameter = True, 0, None
            continue
        if not in_block:
            continue
        if line == "---":
            fences += 1
            if fences >= 2:
                in_block = False
            continue
        match = _PARAM.match(line)
        if match:
            parameter = (match.group(1), match.group(2))
            continue
        match = _TYPE.match(line)
        if match and parameter is not None:
            findings.append(
                Finding(endpoint, *parameter, match.group(1).strip())
            )
    return findings


def scan_completed(output: str) -> bool:
    """True when sqlmap ran to its normal end and gave a verdict.

    A complete run ends with the end marker and either an injection block
    or the explicit verdict "all tested parameters do not appear to be
    injectable" (which sqlmap itself logs as CRITICAL). Any other CRITICAL
    line (unreachable target, 401/404 on the first request, no testable
    parameter, absent -p name) means the parameters were not tested, so an
    unknown CRITICAL fails closed.
    """
    if not _END.search(output):
        return False
    for match in _CRITICAL.finditer(output):
        if not any(text in match.group(1) for text in _BENIGN_CRITICAL):
            return False
    return _VERDICT in output or _HEADER in output


def _cmd_plan(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.openapi).read_text(encoding="utf-8"))
    values: dict[str, Any] = {}
    if args.values:
        values = json.loads(Path(args.values).read_text(encoding="utf-8"))
    plan = generate_plan(doc, Path(args.out_dir), args.host, values)
    ops = plan["operations"]
    scanned = sum(1 for o in ops if o["scan"])
    print(f"{len(ops)} operations, {scanned} with parameters to scan")
    return 0


def _cmd_findings(args: argparse.Namespace) -> int:
    output = Path(args.output).read_text(encoding="utf-8", errors="replace")
    found = parse_findings(output, args.endpoint)
    for item in found:
        print(
            f"INJECTION {item.endpoint} parameter={item.parameter} "
            f"place={item.place} technique={item.technique}"
        )
    if found:
        return 1
    if not scan_completed(output):
        print(
            f"INCOMPLETE {args.endpoint}: no verdict or a critical error, "
            "see the sqlmap log"
        )
        return 2
    return 0


def _cmd_jobs(args: argparse.Namespace) -> int:
    """Print one tab-separated line per sqlmap run.

    Fields: label, request file, authenticated (0/1), -p value, HTTP codes
    for ``--ignore-code`` and endpoint; an empty field is written as ``-``
    because the shell would merge adjacent tabs.

    sqlmap stops on a 401 answer to its first request unless told
    otherwise, and a public sign-in operation answers 401 to a dummy
    credential. An authenticated operation never ignores 401, so an invalid
    token cannot pass as a clean scan.
    """
    plan = json.loads(
        (Path(args.plan_dir) / "plan.json").read_text(encoding="utf-8")
    )

    def clean(text: str) -> str:
        return "".join(c if c.isprintable() else "_" for c in text) or "-"

    for op in plan["operations"]:
        ignored = "-" if op["authenticated"] else "401"
        for index, job in enumerate(op.get("jobs", [])):
            print(
                "\t".join(
                    [
                        f"{op['id']}.{index}",
                        job["file"],
                        "1" if op["authenticated"] else "0",
                        clean(",".join(job["test_parameters"])),
                        ignored,
                        clean(f"{op['method']} {op['path']}"),
                    ]
                )
            )
    return 0


REACHED = "reached"
NOT_REACHED = "not_reached"
FAILED = "failed"


def classify_baseline(status: int | None, public: bool) -> str:
    """Judge the answer to the unmodified generated request.

    A parameter is only tested if the handler can see it, which the first
    answer shows: 2xx, or the 401 a public sign-in gives a dummy credential.
    Any other 4xx means validation, scoping or a missing object stopped the
    request before its fields reached SQL (not reached, not an error). A 5xx
    or no answer at all is a broken target and fails the job.
    """
    if status is None or status >= 500:
        return FAILED
    if 200 <= status < 300 or (public and status == 401):
        return REACHED
    return NOT_REACHED


def fetch_baseline(
    request_file: Path, token: str | None = None, timeout: float = 20.0
) -> int | None:
    """Send the request file once; return the HTTP status or ``None``."""
    raw = request_file.read_bytes().decode("utf-8", errors="replace")
    head, _, body = raw.partition("\r\n\r\n")
    lines = head.split("\r\n")
    method, target, _version = lines[0].split(" ", 2)
    headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    host = headers.pop("Host")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://{host}{target.replace('*', '')}",
        data=body.encode("utf-8") if body else None,
        headers=headers,
        method=method,
    )
    # The scheme is fixed to http above, so urlopen cannot open a file: URL.
    opener = urllib.request.urlopen  # nosec B310 - http scheme is fixed above
    try:
        with opener(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (OSError, ValueError):
        return None


def _cmd_baseline(args: argparse.Namespace) -> int:
    token = os.environ.get(args.token_env) if args.token_env else None
    status = fetch_baseline(Path(args.request_file), token)
    verdict = classify_baseline(status, args.public)
    print(f"{verdict} {status if status is not None else 'no-answer'}")
    return {REACHED: 0, NOT_REACHED: 3}.get(verdict, 2)


def _read_baseline(path: Path) -> tuple[str, str, str]:
    """Parse ``<verdict> <status> <token name>``; unknown fails closed."""
    try:
        verdict, status, token = path.read_text(encoding="utf-8").split()[:3]
    except (OSError, ValueError):
        return NOT_REACHED, "unknown", "-"
    if verdict not in (REACHED, NOT_REACHED, FAILED):
        return NOT_REACHED, "unknown", "-"
    return verdict, status, token


def build_coverage(reports: Path) -> tuple[Json, list[str], str]:
    """Merge per-run baselines into the plan; return plan, lines, summary."""
    plan = json.loads((reports / "plan.json").read_text(encoding="utf-8"))
    lines: list[str] = []
    reached = missed = ops_reached = ops_total = 0
    for op in plan["operations"]:
        jobs = op.get("jobs", [])
        if not jobs:
            continue
        ops_total += 1
        results = []
        for index, job in enumerate(jobs):
            label = f"{op['id']}.{index}"
            verdict, status, token = _read_baseline(
                reports / "sqlmap" / label / "baseline"
            )
            results.append(
                {"job": label, "verdict": verdict, "status": status}
                | {"token": token, "parameters": job["test_parameters"]}
            )
        op["baseline"] = results
        op_reached = all(r["verdict"] == REACHED for r in results)
        ops_reached += op_reached
        shown = ", ".join(f"{r['status']}" for r in results)
        for param in op["parameters"]:
            if param["status"] != "scanned":
                continue
            if op_reached:
                reached += 1
                param["baseline_status"] = results[0]["status"]
            else:
                missed += 1
                param["status"] = NOT_REACHED
                param["reason"] = f"not reached (status {shown})"
        word = "REACHED" if op_reached else "NOT_REACHED"
        names = ",".join(sorted({p for r in results for p in r["parameters"]}))
        lines.append(
            f"{word} status={shown} token={results[0]['token']} "
            f"{op['method']} {op['path']} parameters={names or 'path'}"
        )
    summary = (
        f"reached {reached} / not reached {missed} of {reached + missed} "
        f"scannable parameters ({ops_reached} of {ops_total} operations "
        "reached)"
    )
    return plan, lines, summary


def _cmd_coverage(args: argparse.Namespace) -> int:
    reports = Path(args.reports_dir)
    plan, lines, summary = build_coverage(reports)
    (reports / "plan.json").write_text(
        json.dumps(plan, indent=2) + "\n", encoding="utf-8"
    )
    (reports / "reached.txt").write_text(
        "\n".join([summary, *sorted(lines)]) + "\n", encoding="utf-8"
    )
    print(f"sqlmap coverage: {summary}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="write request files and plan.json")
    plan.add_argument("openapi")
    plan.add_argument("out_dir")
    plan.add_argument("--host", default=DEFAULT_HOST)
    plan.add_argument(
        "--values", help="JSON file: parameter or field name -> value"
    )
    plan.set_defaults(func=_cmd_plan)
    find = sub.add_parser("findings", help="check one sqlmap output file")
    find.add_argument("output")
    find.add_argument("endpoint")
    find.set_defaults(func=_cmd_findings)
    base = sub.add_parser("baseline", help="send one request, judge status")
    base.add_argument("request_file")
    base.add_argument("--token-env", help="env var holding a bearer token")
    base.add_argument("--public", action="store_true")
    base.set_defaults(func=_cmd_baseline)
    cover = sub.add_parser("coverage", help="merge baselines into plan.json")
    cover.add_argument("reports_dir")
    cover.set_defaults(func=_cmd_coverage)
    jobs = sub.add_parser("jobs", help="list the sqlmap runs of a plan")
    jobs.add_argument("plan_dir")
    jobs.set_defaults(func=_cmd_jobs)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

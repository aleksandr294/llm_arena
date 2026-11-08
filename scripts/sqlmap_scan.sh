#!/usr/bin/env bash
# Scan stage of the sqlmap CI job. Runs inside a python:3.12-slim container
# (see scripts/ci_sqlmap.sh): installs the pinned sqlmap, builds the scan plan
# from an OpenAPI document, sends each planned request once to see whether
# its parameters can be reached, and runs sqlmap on those that can.
#
# Usage: scripts/sqlmap_scan.sh <openapi.json> <host:port> <reports-dir>
#        [<values.json>]
# Env:   SCAN_TOKEN        superadmin bearer token (optional)
#        SCAN_ADMIN_TOKEN  organization admin bearer token (optional); an
#                          authenticated operation uses the first of the two
#                          whose baseline request answers 2xx
#        SQLMAP_LEVEL      sqlmap --level (default 2)
#        SQLMAP_PARALLEL   concurrent sqlmap runs (default 4)
#        SQLMAP_BUDGET     seconds for the whole scan (default 3000); the
#                          per-run limit is derived from it so that the scan
#                          fits the 60 minute job timeout
#        SQLMAP_JOB_LIMIT  per-run limit in seconds, overrides the derived one
#        SQLMAP_CMD        sqlmap executable to use instead of installing the
#                          pinned release (tests and local checks only)
#        PYTHON            interpreter for the helper script (default python)
#
# Exit status: 0 clean, 1 a confirmed injection (findings.txt names it),
# 2 a run that did not complete, a baseline server error or connection
# failure, or an empty plan (scan_failures.txt), and any other non-zero value
# for a setup failure. Parameters that cannot be reached (validation, scope
# or a missing object answered 4xx) are listed in reached.txt, not failed.
set -euo pipefail

if [ "$#" -ne 3 ] && [ "$#" -ne 4 ]; then
  echo "usage: $0 <openapi.json> <host:port> <reports-dir> [<values.json>]" >&2
  exit 64
fi

SQLMAP_VERSION="1.10.11"
OPENAPI="$1"
HOST="$2"
REPORTS="$3"
VALUES="${4:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python}"
PLAN_DIR="$REPORTS/plan"
OUT_DIR="$REPORTS/sqlmap"
LIB_DIR="$(mktemp -d)"
PARALLEL="${SQLMAP_PARALLEL:-4}"
export SQLMAP_LEVEL="${SQLMAP_LEVEL:-2}"
export PLAN_DIR OUT_DIR HERE LIB_DIR PY
export PYTHONPATH="$LIB_DIR${PYTHONPATH:+:$PYTHONPATH}"

# sqlmap's session files hold the request, including the Authorization
# header, so they are removed on every way out, a killed run included.
scrub() {
  rm -rf "$LIB_DIR"
  [ -d "$OUT_DIR" ] || return 0
  find "$OUT_DIR" -type f \( -name 'target.txt' -o -name 'session.sqlite*' \
    -o -name 'hashes.txt' \) -delete 2>/dev/null || true
  find "$OUT_DIR" -type d -name dump -prune -exec rm -rf {} + 2>/dev/null ||
    true
}
trap scrub EXIT

if [ -n "${SQLMAP_CMD:-}" ]; then
  SQLMAP="$SQLMAP_CMD"
else
  # --no-cache-dir and --target keep the image and the reports untouched.
  pip install --quiet --no-cache-dir --disable-pip-version-check \
    --root-user-action=ignore \
    --target "$LIB_DIR" "sqlmap==${SQLMAP_VERSION}"
  SQLMAP="$PY $LIB_DIR/bin/sqlmap"
fi
export SQLMAP

mkdir -p "$OUT_DIR"
PLAN_ARGS=(--host "$HOST")
if [ -n "$VALUES" ]; then
  PLAN_ARGS+=(--values "$VALUES")
fi
"$PY" "$HERE/sqlmap_plan.py" plan "$OPENAPI" "$PLAN_DIR" "${PLAN_ARGS[@]}"
cp "$PLAN_DIR/plan.json" "$REPORTS/plan.json"
"$PY" "$HERE/sqlmap_plan.py" jobs "$PLAN_DIR" >"$REPORTS/jobs.tsv"

if [ ! -s "$REPORTS/jobs.tsv" ]; then
  echo "the plan has no parameter to scan" >&2
  echo "INCOMPLETE plan: no parameter to scan" >"$REPORTS/scan_failures.txt"
  : >"$REPORTS/findings.txt"
  : >"$REPORTS/reached.txt"
  exit 2
fi

# Worst case the runs take ceil(jobs / parallel) rounds of the per-run limit.
jobs_total="$(wc -l <"$REPORTS/jobs.tsv")"
rounds=$(((jobs_total + PARALLEL - 1) / PARALLEL))
limit=$((${SQLMAP_BUDGET:-3000} / rounds))
[ "$limit" -gt 1500 ] && limit=1500
[ "$limit" -lt 60 ] && limit=60
export SQLMAP_JOB_LIMIT="${SQLMAP_JOB_LIMIT:-$limit}"

run_job() { # <tab-separated: label file auth params ignore endpoint>
  local label file auth params ignore endpoint
  IFS=$'\t' read -r label file auth params ignore endpoint <<<"$1"
  local dir="$OUT_DIR/$label"
  mkdir -p "$dir"
  local start=$SECONDS

  # Baseline: the unmodified request, once, with the token that works.
  local candidates=("-:") public=() chosen_name="-" chosen_var=""
  local statuses="" first_name="" reached=0 failed=0 out brc word status
  if [ "$auth" = "1" ]; then
    candidates=()
    [ -n "${SCAN_TOKEN:-}" ] && candidates+=("superadmin:SCAN_TOKEN")
    [ -n "${SCAN_ADMIN_TOKEN:-}" ] && candidates+=("admin:SCAN_ADMIN_TOKEN")
    [ "${#candidates[@]}" -gt 0 ] || candidates=("-:")
  else
    public=(--public)
  fi
  local cand name var targs
  for cand in "${candidates[@]}"; do
    name="${cand%%:*}"
    var="${cand#*:}"
    targs=()
    [ -n "$var" ] && targs=(--token-env "$var")
    brc=0
    out="$("$PY" "$HERE/sqlmap_plan.py" baseline "$PLAN_DIR/$file" \
      "${targs[@]}" "${public[@]}" 2>/dev/null)" || brc=$?
    word="${out%% *}"
    status="${out##* }"
    [ -n "$first_name" ] || first_name="$name"
    if [ "$brc" -eq 0 ] && [ "$word" = "reached" ]; then
      reached=1
      chosen_name="$name"
      chosen_var="$var"
      statuses="$status"
      break
    fi
    statuses="${statuses:+$statuses,}$status"
    if [ "$brc" -ne 3 ]; then
      failed=1
      break
    fi
  done

  if [ "$failed" -eq 1 ]; then
    echo "failed $statuses $first_name" >"$dir/baseline"
    echo "INCOMPLETE $endpoint: baseline request failed (status $statuses)" \
      >"$dir/result.txt"
    echo 2 >"$dir/verdict"
    echo "sqlmap $label ($endpoint): baseline failed ($statuses)"
    return 0
  fi
  if [ "$reached" -ne 1 ]; then
    echo "not_reached $statuses $first_name" >"$dir/baseline"
    : >"$dir/result.txt"
    echo 0 >"$dir/verdict"
    echo "sqlmap $label ($endpoint): not reached (status $statuses)"
    return 0
  fi
  echo "reached $statuses $chosen_name" >"$dir/baseline"

  local args=(--batch --level "$SQLMAP_LEVEL" --risk 1 --dbms PostgreSQL
    --timeout 15 --retries 1 --threads 1 --flush-session --disable-coloring
    --output-dir "$dir/data" -r "$PLAN_DIR/$file")
  if [ "$params" != "-" ]; then
    args+=(-p "$params")
  fi
  if [ "$ignore" != "-" ]; then
    args+=(--ignore-code "$ignore")
  fi
  if [ -n "$chosen_var" ]; then
    # Visible in the process list of this throwaway container only.
    args+=(--headers "Authorization: Bearer ${!chosen_var}")
  fi
  local rc=0
  # shellcheck disable=SC2086  # SQLMAP is "interpreter script" or one path
  timeout "$SQLMAP_JOB_LIMIT" $SQLMAP "${args[@]}" \
    >"$dir/sqlmap.log" 2>&1 || rc=$?
  local verdict=0
  "$PY" "$HERE/sqlmap_plan.py" findings "$dir/sqlmap.log" "$endpoint" \
    >"$dir/result.txt" 2>&1 || verdict=$?
  if [ "$rc" -ne 0 ] && [ "$verdict" -eq 0 ]; then
    verdict=2
    echo "INCOMPLETE $endpoint: sqlmap exited with status $rc" \
      >"$dir/result.txt"
  fi
  echo "$verdict" >"$dir/verdict"
  echo "sqlmap $label ($endpoint): verdict $verdict, $((SECONDS - start))s"
}
export -f run_job

xargs -d '\n' -P "$PARALLEL" -n 1 bash -c 'run_job "$0"' \
  <"$REPORTS/jobs.tsv"

"$PY" "$HERE/sqlmap_plan.py" coverage "$REPORTS"

# Both files are always written so the artifact tells the whole story; a
# confirmed injection (1) is reported ahead of an incomplete run (2).
: >"$REPORTS/findings.txt"
: >"$REPORTS/scan_failures.txt"
while IFS=$'\t' read -r label _ _ _ _ endpoint; do
  verdict="$(cat "$OUT_DIR/$label/verdict" 2>/dev/null || echo 2)"
  if [ "$verdict" = "1" ]; then
    cat "$OUT_DIR/$label/result.txt" >>"$REPORTS/findings.txt"
  elif [ "$verdict" != "0" ]; then
    {
      cat "$OUT_DIR/$label/result.txt" 2>/dev/null ||
        echo "INCOMPLETE $endpoint: no result"
    } >>"$REPORTS/scan_failures.txt"
  fi
done <"$REPORTS/jobs.tsv"

status=0
if [ -s "$REPORTS/scan_failures.txt" ]; then
  echo "scan did not complete for:" >&2
  cat "$REPORTS/scan_failures.txt" >&2
  status=2
fi
if [ -s "$REPORTS/findings.txt" ]; then
  echo "confirmed injection(s):" >&2
  cat "$REPORTS/findings.txt" >&2
  status=1
fi
exit "$status"

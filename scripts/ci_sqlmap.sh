#!/usr/bin/env bash
# Throwaway sqlmap SQL injection scan used by CI and usable locally.
#
# Usage: scripts/ci_sqlmap.sh <reports-dir>
#        scripts/ci_sqlmap.sh --scan-only <reports-dir> <openapi.json> <host:port>
#
# Builds the image, starts PostgreSQL, RabbitMQ and the API on a private
# Docker network with generated credentials, seeds a little data through the
# REST API, builds a scan plan from /openapi.json and runs sqlmap against the
# string parameters of every operation (scripts/sqlmap_scan.sh, inside a
# python:3.12-slim container). A confirmed injection, a scan that did not
# complete, a failed setup or a credential found in the reports fails the
# script. Everything created here is removed on exit.
#
# --scan-only exists for local verification of the failure path only: it skips
# the instance and scans a target on the host (for example a deliberately
# vulnerable stub kept outside the repository) through the same scan stage.
set -euo pipefail

SCAN_ONLY=0
if [ "${1:-}" = "--scan-only" ]; then
  SCAN_ONLY=1
  shift
  if [ "$#" -ne 3 ]; then
    echo "usage: $0 --scan-only <reports-dir> <openapi.json> <host:port>" >&2
    exit 2
  fi
  OPENAPI_FILE="$2"
  SCAN_HOST="$3"
elif [ "$#" -ne 1 ]; then
  echo "usage: $0 <reports-dir>" >&2
  exit 2
fi

REPORTS_DIR="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUFFIX="$$-$(openssl rand -hex 3)"
PREFIX="ci-sqlmap-${SUFFIX}"
NET="${PREFIX}-net"
IMAGE="${PREFIX}:run"
PG="${PREFIX}-pg"
MQ="${PREFIX}-mq"
API="${PREFIX}-api"
SCAN_NAME="${PREFIX}-scan"
SCAN_IMAGE="python:3.12-slim"
APP_PORT=8000

# The reports are published as an artifact, also after a failed or killed
# run, so this runs on every exit: it removes sqlmap session files (they hold
# the Authorization header) and refuses the reports if any holds a secret. The
# grep only matches the raw values, so deleting the session files is the
# real defence; the grep is the backstop.
sanitize_reports() {
  [ -d "${REPORTS_DIR:-}" ] || return 0
  find "$REPORTS_DIR" -type f \( -name 'target.txt' -o -name 'session.sqlite*' \
    -o -name 'hashes.txt' \) -delete 2>/dev/null || true
  find "$REPORTS_DIR" -type d -name dump -prune -exec rm -rf {} + \
    2>/dev/null || true
  local secret
  for secret in "${SCAN_TOKEN:-}" "${SCAN_ADMIN_TOKEN:-}" \
    "${SUPERADMIN_PASSWORD:-}" "${SEED_ADMIN_PASSWORD:-}" \
    "${SEED_TARGET_PASSWORD:-}" "${PG_PASSWORD:-}" "${MQ_PASSWORD:-}" \
    "${JWT_SECRET:-}" "${ADMIN_SECRET:-}" "${PROVIDER_KEY:-}"; do
    [ -n "$secret" ] || continue
    if grep -rqF -- "$secret" "$REPORTS_DIR"; then
      echo "a credential was found in the sqlmap reports; removing them" >&2
      find "$REPORTS_DIR" -type f -delete
      return 1
    fi
  done
}

cleanup() {
  local status=$?
  docker rm -f "$API" "$PG" "$MQ" "$SCAN_NAME" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker rmi "$IMAGE" >/dev/null 2>&1 || true
  if ! sanitize_reports; then
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT

# Generated, per-run, never printed. Hex keeps them URL-safe.
PG_USER="sq"
PG_DB="sq"
PG_PASSWORD="$(openssl rand -hex 16)"
MQ_USER="sq"
MQ_PASSWORD="$(openssl rand -hex 16)"
MQ_COOKIE="$(openssl rand -hex 16)"
JWT_SECRET="$(openssl rand -hex 32)"
ADMIN_SECRET="$(openssl rand -hex 32)"
SUPERADMIN_EMAIL="sqlmap@example.com"
SUPERADMIN_PASSWORD="$(openssl rand -hex 16)"
SEED_ADMIN_EMAIL="seed-admin@example.com"
SEED_ADMIN_PASSWORD="$(openssl rand -hex 16)"
SEED_TARGET_PASSWORD="$(openssl rand -hex 16)"
# A placeholder so experiments can be created (no model is ever called).
PROVIDER_KEY="sk-throwaway-$(openssl rand -hex 8)"
SCAN_TOKEN=""
SCAN_ADMIN_TOKEN=""
export SCAN_TOKEN SCAN_ADMIN_TOKEN

mkdir -p "$REPORTS_DIR"
REPORTS_DIR="$(cd "$REPORTS_DIR" && pwd)"

# The scan container runs as the calling user, so the reports stay removable.
# With rootless Docker the caller already is the container's root user, and
# naming a user would map it to an unprivileged one that cannot write.
USER_ARGS=(--user "$(id -u):$(id -g)")
if docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
  USER_ARGS=()
fi
scan_stage() { # <openapi path> <host:port> <values path> [docker args]
  local openapi="$1" host="$2" values="$3"
  shift 3
  docker run --rm --name "$SCAN_NAME" "${USER_ARGS[@]}" \
    -e HOME=/tmp -e SCAN_TOKEN -e SCAN_ADMIN_TOKEN \
    -v "$ROOT/scripts:/work/scripts:ro,z" \
    -v "$REPORTS_DIR:/work/reports:rw,z" \
    "$@" "$SCAN_IMAGE" \
    bash /work/scripts/sqlmap_scan.sh "/work/reports/$openapi" "$host" \
    /work/reports "/work/reports/$values"
}

if [ "$SCAN_ONLY" = "1" ]; then
  cp "$OPENAPI_FILE" "$REPORTS_DIR/openapi.json"
  scan_rc=0
  echo '{}' >"$REPORTS_DIR/values.json"
  # A host name is mapped to the Docker host; an IP address is used as is.
  HOST_ARGS=()
  if ! [[ "${SCAN_HOST%%:*}" =~ ^[0-9.]+$ ]]; then
    HOST_ARGS=(--add-host "${SCAN_HOST%%:*}:host-gateway")
  fi
  scan_stage openapi.json "$SCAN_HOST" values.json \
    "${HOST_ARGS[@]}" || scan_rc=$?
  exit "$scan_rc"
fi

docker pull "$SCAN_IMAGE" >/dev/null
docker build --load -q -t "$IMAGE" "$ROOT" >/dev/null
docker network create "$NET" >/dev/null

docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER="$PG_USER" -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -e POSTGRES_DB="$PG_DB" postgres:17 >/dev/null
docker run -d --name "$MQ" --network "$NET" \
  -e RABBITMQ_DEFAULT_USER="$MQ_USER" \
  -e RABBITMQ_DEFAULT_PASS="$MQ_PASSWORD" \
  -e RABBITMQ_ERLANG_COOKIE="$MQ_COOKIE" \
  rabbitmq:3.13 >/dev/null

ENVS=(
  -e "JWT_SECRET_KEY=$JWT_SECRET"
  -e "ADMIN_SECRET_KEY=$ADMIN_SECRET"
  -e "API_DOCS_ENABLED=true"
  -e "OPENAI_API_KEY=$PROVIDER_KEY"
  # Scans can outlast the 15 minute default; the instance is throwaway.
  -e "JWT_ACCESS_TTL_SECONDS=7200"
  -e "API_HOST=0.0.0.0"
  -e "DATABASE_URL=postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@${PG}:5432/${PG_DB}"
  -e "RABBITMQ_URL=amqp://${MQ_USER}:${MQ_PASSWORD}@${MQ}:5672/"
)

wait_for() { # <description> <attempts> <command...>
  local what="$1" attempts="$2" i
  shift 2
  for ((i = 1; i <= attempts; i++)); do
    if "$@" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "timed out waiting for $what" >&2
  return 1
}

pg_ready() { docker exec "$PG" pg_isready -h 127.0.0.1 -U "$PG_USER" -d "$PG_DB"; }
# As the rabbitmq user: a root exec makes the CLI create a root-owned
# .erlang.cookie in the data directory while the server is still starting,
# and the server then fails with eacces reading it.
mq_ready() { docker exec -u rabbitmq "$MQ" rabbitmq-diagnostics -q ping; }

if ! wait_for "PostgreSQL" 45 pg_ready; then
  docker logs --tail 40 "$PG" >&2 || true
  exit 1
fi
if ! wait_for "RabbitMQ" 90 mq_ready; then
  docker logs --tail 40 "$MQ" >&2 || true
  exit 1
fi

docker run --rm --network "$NET" "${ENVS[@]}" "$IMAGE" \
  alembic upgrade head >/dev/null
docker run --rm --network "$NET" "${ENVS[@]}" \
  -e "SUPERADMIN_PASSWORD=$SUPERADMIN_PASSWORD" "$IMAGE" \
  python cli.py create-superadmin --email "$SUPERADMIN_EMAIL" >/dev/null

docker run -d --name "$API" --network "$NET" "${ENVS[@]}" \
  --security-opt no-new-privileges:true "$IMAGE" >/dev/null

api_ready() {
  docker exec "$API" python -c \
    "import urllib.request as u; u.urlopen('http://localhost:${APP_PORT}/api/v1/health/ready', timeout=5)"
}
if ! wait_for "the API readiness endpoint" 45 api_ready; then
  docker logs --tail 40 "$API" >&2 || true
  exit 1
fi

# Log in from inside the API container: no host port is needed and the token
# is only ever held in this variable.
SCAN_TOKEN="$(docker exec -e "LOGIN_EMAIL=$SUPERADMIN_EMAIL" \
  -e "LOGIN_PASSWORD=$SUPERADMIN_PASSWORD" "$API" python -c '
import json, os, urllib.request as u
body = json.dumps({"email": os.environ["LOGIN_EMAIL"],
                   "password": os.environ["LOGIN_PASSWORD"]}).encode()
req = u.Request("http://localhost:'"$APP_PORT"'/api/v1/auth/login", body,
                {"Content-Type": "application/json"})
print(json.load(u.urlopen(req, timeout=10))["access_token"])
')"
if [ -z "$SCAN_TOKEN" ]; then
  echo "login did not return an access token" >&2
  exit 1
fi
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  # Masks the value in every later log line of the job.
  echo "::add-mask::$SCAN_TOKEN"
fi

# The org admin token is the second credential: organization-scoped
# operations answer 403/404 to a superadmin (it has no organization), so the
# scan stage picks per operation the token whose baseline request succeeds.
login_token() { # <email> <password>
  docker exec -e "LOGIN_EMAIL=$1" -e "LOGIN_PASSWORD=$2" "$API" python -c '
import json, os, urllib.request as u
body = json.dumps({"email": os.environ["LOGIN_EMAIL"],
                   "password": os.environ["LOGIN_PASSWORD"]}).encode()
req = u.Request("http://localhost:'"$APP_PORT"'/api/v1/auth/login", body,
                {"Content-Type": "application/json"})
print(json.load(u.urlopen(req, timeout=10))["access_token"])
'
}

# Seed through the REST API so list and filter endpoints return rows (blind
# techniques need responses that differ) and so the generated requests refer
# to objects that exist: an unknown id answers 404 before a body field is
# used. A superadmin belongs to no organization and cannot own a dataset, so
# the dataset, test case and experiment are created by the seeded
# organization admin. The experiment needs a configured provider, which the
# placeholder key provides; creating it makes no model call. values.json
# holds ids and dummy field values only, never a credential. Nothing here is
# printed.
docker exec -i -e "SUPERADMIN_EMAIL=$SUPERADMIN_EMAIL" \
  -e "SUPERADMIN_PASSWORD=$SUPERADMIN_PASSWORD" \
  -e "SEED_ADMIN_EMAIL=$SEED_ADMIN_EMAIL" \
  -e "SEED_ADMIN_PASSWORD=$SEED_ADMIN_PASSWORD" \
  -e "SEED_TARGET_PASSWORD=$SEED_TARGET_PASSWORD" \
  "$API" python - >"$REPORTS_DIR/values.json" <<'PY'
import json
import os
import urllib.request as u

BASE = "http://localhost:8000/api/v1"


def call(method, path, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = u.Request(
        BASE + path, json.dumps(body).encode(), headers, method=method
    )
    return json.load(u.urlopen(req, timeout=15))


def login(email, password):
    body = {"email": email, "password": password}
    return call("POST", "/auth/login", body)["access_token"]


root = login(os.environ["SUPERADMIN_EMAIL"], os.environ["SUPERADMIN_PASSWORD"])
org = call("POST", "/organizations", {"name": "Seed organization"}, root)
call(
    "POST",
    "/users",
    {
        "email": os.environ["SEED_ADMIN_EMAIL"],
        "password": os.environ["SEED_ADMIN_PASSWORD"],
        "role": "admin",
        "organization_id": org["id"],
    },
    root,
)
# A second, throwaway user the scan may modify (PATCH /users/{user_id}); the
# admin whose token is used must keep working.
target = call(
    "POST",
    "/users",
    {
        "email": "scan-target@example.com",
        "password": os.environ["SEED_TARGET_PASSWORD"],
        "role": "staff",
        "organization_id": org["id"],
    },
    root,
)
admin = login(os.environ["SEED_ADMIN_EMAIL"], os.environ["SEED_ADMIN_PASSWORD"])
dataset = call(
    "POST", "/datasets", {"name": "Seed dataset", "description": "seed"}, admin
)
call(
    "POST",
    "/datasets/" + dataset["id"] + "/cases",
    {"input": "What is 2 + 2?", "expected_output": "4"},
    admin,
)
experiment = call(
    "POST",
    "/experiments",
    {
        "name": "Seed experiment",
        "dataset_id": dataset["id"],
        "prompt_template": "Question: {input}",
        "config": {"targets": [{"provider": "openai", "model": "seed-model"}]},
    },
    admin,
)
# Names here replace the generated value of any path, query or body field of
# that name. The password is a dummy that matches no user.
print(
    json.dumps(
        {
            "organization_id": org["id"],
            "dataset_id": dataset["id"],
            "user_id": target["id"],
            "experiment_id": experiment["id"],
            "prompt_template": "Question: {input}",
            "email": "scan-new@example.com",
            "password": "dummy-password-123",
            "role": "staff",
            # POST /organizations creates "x"; the rename must not clash.
            "PATCH /api/v1/organizations/{organization_id}:name": "Renamed",
        }
    )
)
PY
if [ ! -s "$REPORTS_DIR/values.json" ]; then
  echo "seeding did not produce values.json" >&2
  exit 1
fi
SCAN_ADMIN_TOKEN="$(login_token "$SEED_ADMIN_EMAIL" "$SEED_ADMIN_PASSWORD")"
if [ -z "$SCAN_ADMIN_TOKEN" ]; then
  echo "admin login did not return an access token" >&2
  exit 1
fi
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  echo "::add-mask::$SCAN_ADMIN_TOKEN"
fi

OPENAPI_PATH="openapi.json"
docker exec "$API" python -c \
  "import urllib.request as u; print(u.urlopen('http://localhost:${APP_PORT}/openapi.json', timeout=15).read().decode())" \
  >"$REPORTS_DIR/$OPENAPI_PATH"
if [ ! -s "$REPORTS_DIR/$OPENAPI_PATH" ]; then
  echo "could not fetch /openapi.json" >&2
  exit 1
fi

scan_rc=0
echo "sqlmap: scanning ${API}:${APP_PORT}"
scan_stage "$OPENAPI_PATH" "${API}:${APP_PORT}" values.json \
  --network "$NET" || scan_rc=$?

for report in plan.json findings.txt scan_failures.txt reached.txt; do
  if [ ! -e "$REPORTS_DIR/$report" ]; then
    echo "missing report: $report" >&2
    scan_rc=2
  fi
done

# The credential check and the removal of session files run in the exit trap.
exit "$scan_rc"

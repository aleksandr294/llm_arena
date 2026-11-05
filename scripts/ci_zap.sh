#!/usr/bin/env bash
# Throwaway OWASP ZAP run used by CI and usable locally.
#
# Usage: scripts/ci_zap.sh <reports-dir>
#
# Builds the image, starts PostgreSQL, RabbitMQ and the API on a private
# Docker network with generated credentials, then runs the three scans
# documented in docs/security.md. ZAP alerts do not fail the script (-I); a
# failed setup, an unhealthy API, a scan that could not run, or a credential
# found in a report does. Everything created here is removed on exit.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <reports-dir>" >&2
  exit 2
fi

REPORTS_DIR="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUFFIX="$$-$(openssl rand -hex 3)"
PREFIX="ci-zap-${SUFFIX}"
NET="${PREFIX}-net"
IMAGE="${PREFIX}:run"
PG="${PREFIX}-pg"
MQ="${PREFIX}-mq"
API="${PREFIX}-api"
ZAP_NAME="${PREFIX}-zap"
ZAP_IMAGE="ghcr.io/zaproxy/zaproxy:stable"
APP_PORT=8000

cleanup() {
  docker rm -f "$API" "$PG" "$MQ" "$ZAP_NAME" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker rmi "$IMAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Generated, per-run, never printed. Hex keeps them URL-safe.
PG_USER="zp"
PG_DB="zp"
PG_PASSWORD="$(openssl rand -hex 16)"
MQ_USER="zq"
MQ_PASSWORD="$(openssl rand -hex 16)"
JWT_SECRET="$(openssl rand -hex 32)"
ADMIN_SECRET="$(openssl rand -hex 32)"
SUPERADMIN_EMAIL="zap@example.com"
SUPERADMIN_PASSWORD="$(openssl rand -hex 16)"
TOKEN=""

mkdir -p "$REPORTS_DIR"
REPORTS_DIR="$(cd "$REPORTS_DIR" && pwd)"
# The ZAP container runs as an unprivileged user and writes into the mount.
chmod 777 "$REPORTS_DIR"

docker pull "$ZAP_IMAGE" >/dev/null
docker build --load -q -t "$IMAGE" "$ROOT" >/dev/null
docker network create "$NET" >/dev/null

docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER="$PG_USER" -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -e POSTGRES_DB="$PG_DB" postgres:17 >/dev/null
docker run -d --name "$MQ" --network "$NET" \
  -e RABBITMQ_DEFAULT_USER="$MQ_USER" \
  -e RABBITMQ_DEFAULT_PASS="$MQ_PASSWORD" \
  rabbitmq:3.13 >/dev/null

ENVS=(
  -e "JWT_SECRET_KEY=$JWT_SECRET"
  -e "ADMIN_SECRET_KEY=$ADMIN_SECRET"
  -e "API_DOCS_ENABLED=true"
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
mq_ready() { docker exec "$MQ" rabbitmq-diagnostics -q ping; }

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
TOKEN="$(docker exec -e "LOGIN_EMAIL=$SUPERADMIN_EMAIL" \
  -e "LOGIN_PASSWORD=$SUPERADMIN_PASSWORD" "$API" python -c '
import json, os, urllib.request as u
body = json.dumps({"email": os.environ["LOGIN_EMAIL"],
                   "password": os.environ["LOGIN_PASSWORD"]}).encode()
req = u.Request("http://localhost:'"$APP_PORT"'/api/v1/auth/login", body,
                {"Content-Type": "application/json"})
print(json.load(u.urlopen(req, timeout=10))["access_token"])
')"
if [ -z "$TOKEN" ]; then
  echo "login did not return an access token" >&2
  exit 1
fi
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  # Masks the value in every later log line of the job.
  echo "::add-mask::$TOKEN"
fi

TARGET="http://${API}:${APP_PORT}"
zap() { # <script> <args...>
  docker run --rm --name "$ZAP_NAME" --network "$NET" \
    -v "$REPORTS_DIR:/zap/wrk:rw,z" \
    "$ZAP_IMAGE" "$@"
}

FAILED=0
run_scan() { # <label> <zap args...>
  local label="$1"
  shift
  echo "ZAP: $label"
  if ! zap "$@" >"$REPORTS_DIR/${label}.log" 2>&1; then
    echo "ZAP scan '$label' failed to run, see $label.log" >&2
    FAILED=1
  fi
}

run_scan baseline zap-baseline.py -t "$TARGET/docs" -m 5 -I \
  -r baseline.html -J baseline.json
run_scan admin zap-baseline.py -t "$TARGET/admin/" -m 3 -I \
  -r admin.html -J admin.json
run_scan api zap-api-scan.py -t "$TARGET/openapi.json" -f openapi \
  -O "$TARGET" -T 5 -I -r api.html -J api.json \
  -z "-config replacer.full_list(0).description=auth \
-config replacer.full_list(0).enabled=true \
-config replacer.full_list(0).matchtype=REQ_HEADER \
-config replacer.full_list(0).matchstr=Authorization \
-config replacer.full_list(0).regex=false \
-config replacer.full_list(0).replacement=Bearer\\ $TOKEN"

# The reports are published as an artifact: refuse if any holds a secret.
for secret in "$TOKEN" "$SUPERADMIN_PASSWORD" "$PG_PASSWORD" \
  "$MQ_PASSWORD" "$JWT_SECRET" "$ADMIN_SECRET"; do
  if grep -rqF -- "$secret" "$REPORTS_DIR"; then
    echo "a credential was found in the ZAP reports; removing them" >&2
    find "$REPORTS_DIR" -type f -delete
    exit 1
  fi
done

for report in baseline.html baseline.json admin.html admin.json \
  api.html api.json; do
  if [ ! -s "$REPORTS_DIR/$report" ]; then
    echo "missing report: $report" >&2
    FAILED=1
  fi
done

exit "$FAILED"

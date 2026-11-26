#!/usr/bin/env bash
# headersHelper for the GitHub MCP server declared in .mcp.json.
# Prints the Authorization header as JSON. The token comes from the
# environment, else from the git-ignored .env; it is never logged and no
# other .env value is read.
set -eu

token="${GITHUB_PERSONAL_ACCESS_TOKEN:-}"
if [ -z "$token" ]; then
    env_file="$(cd "$(dirname "$0")/.." && pwd)/.env"
    if [ -f "$env_file" ]; then
        token="$(sed -n -E \
            's/^[[:space:]]*(export[[:space:]]+)?GITHUB_PERSONAL_ACCESS_TOKEN[[:space:]]*=[[:space:]]*//p' \
            "$env_file" | tail -n 1 | sed -E "s/[[:space:]]+$//; s/^(['\"])(.*)\1$/\2/")"
    fi
fi

if [ -z "$token" ]; then
    echo "GITHUB_PERSONAL_ACCESS_TOKEN is not set (environment or .env)" >&2
    exit 1
fi

printf '{"Authorization": "Bearer %s"}\n' "$token"

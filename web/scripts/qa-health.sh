#!/usr/bin/env bash
# web/scripts/qa-health.sh
# QA script: verify PHP built-in server returns HTTP 200 "OK" on GET /
#
# Usage: bash web/scripts/qa-health.sh [--port 8000]

set -euo pipefail

PORT="${1:---port}"
if [ "$PORT" = "--port" ] && [ -n "${2:-}" ]; then
  PORT="$2"
else
  PORT="8000"
fi

HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
DOCROOT="$(cd "$(dirname "$0")/../public" && pwd)"
CONFIG="$(cd "$(dirname "$0")/../config" && pwd)/local.php"

if [ ! -f "$CONFIG" ]; then
  echo "ERROR: $CONFIG not found."
  echo "  Copy web/config/local.php.example -> web/config/local.php"
  exit 1
fi

echo "==> Starting PHP built-in server on $BASE_URL ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server.log 2>&1 &
PHP_PID=$!
trap 'kill $PHP_PID 2>/dev/null; echo "==> Server stopped."' EXIT

# Wait for server to start
for i in $(seq 1 10); do
  curl -sf "$BASE_URL/" >/dev/null 2>&1 && break
  sleep 0.3
done

echo "==> GET / ..."
RESPONSE=$(curl -sf -w '\nHTTP_STATUS:%{http_code}' "$BASE_URL/")
BODY=$(echo "$RESPONSE" | head -1)
STATUS=$(echo "$RESPONSE" | grep 'HTTP_STATUS:' | cut -d: -f2)

echo "  Status : $STATUS"
echo "  Body   : $BODY"

if [ "$STATUS" = "200" ] && echo "$BODY" | grep -q "OK"; then
  echo "PASS: GET / returned 200 OK"
else
  echo "FAIL: Unexpected response"
  exit 1
fi

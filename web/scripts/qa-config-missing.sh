#!/usr/bin/env bash
# web/scripts/qa-config-missing.sh
# QA scenario: verify the app fails safely when local.php is missing.
# Expected: HTTP 500, body contains non-sensitive error, no stack trace.
#
# Usage: bash web/scripts/qa-config-missing.sh [--port 8001]

set -euo pipefail

PORT="${1:---port}"
if [ "$PORT" = "--port" ] && [ -n "${2:-}" ]; then
  PORT="$2"
else
  PORT="8001"
fi

HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
DOCROOT="$(cd "$(dirname "$0")/../public" && pwd)"
CONFIG="$(cd "$(dirname "$0")/../config" && pwd)/local.php"

# Ensure local.php does NOT exist for this test
if [ -f "$CONFIG" ]; then
  echo "INFO: Temporarily renaming local.php -> local.php.bak for test..."
  mv "$CONFIG" "${CONFIG}.bak"
  RESTORE_CONFIG=1
else
  RESTORE_CONFIG=0
fi

cleanup() {
  kill "$PHP_PID" 2>/dev/null || true
  if [ "${RESTORE_CONFIG:-0}" = "1" ] && [ -f "${CONFIG}.bak" ]; then
    mv "${CONFIG}.bak" "$CONFIG"
    echo "==> Restored local.php"
  fi
  echo "==> Server stopped."
}
trap cleanup EXIT

echo "==> Starting PHP built-in server on $BASE_URL (no config) ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server-noconfig.log 2>&1 &
PHP_PID=$!

# Wait for server to start
for i in $(seq 1 10); do
  curl -sf --max-time 1 "$BASE_URL/" >/dev/null 2>&1 && break
  sleep 0.3
done

echo "==> GET / (no config expected to fail safely) ..."
RESPONSE=$(curl -s -w '\nHTTP_STATUS:%{http_code}' "$BASE_URL/")
BODY=$(echo "$RESPONSE" | head -1)
STATUS=$(echo "$RESPONSE" | grep 'HTTP_STATUS:' | cut -d: -f2)

echo "  Status : $STATUS"
echo "  Body   : $BODY"

# Check: no stack trace in body (no "Traceback", no "Exception", no file paths)
STACK_TRACE_FOUND=0
if echo "$BODY" | grep -qiE '(Traceback|stack trace|ErrorException|Fatal error|in /.+\.php|line [0-9]+)'; then
  STACK_TRACE_FOUND=1
fi

if [ "$STATUS" = "500" ] && [ "$STACK_TRACE_FOUND" = "0" ]; then
  echo "PASS: HTTP 500 returned with non-sensitive message (no stack trace)"
else
  echo "FAIL: Expected HTTP 500 and no stack trace"
  echo "  Status: $STATUS, Stack trace found: $STACK_TRACE_FOUND"
  exit 1
fi

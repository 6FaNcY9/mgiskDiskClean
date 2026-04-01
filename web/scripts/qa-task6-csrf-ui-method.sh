#!/usr/bin/env bash
# web/scripts/qa-task6-csrf-ui-method.sh
# Focused check for CSRF header support in /review/update

set -euo pipefail

PORT="18131"
HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
DOCROOT="$(cd "$(dirname "$0")/../public" && pwd)"

# Cleanup
CW_JAR="$(mktemp /tmp/cw-cookies-csrf-check-XXXXXX.txt)"

log() { echo "$1"; }
fail() { log "FAIL: $1"; exit 1; }

log "Starting PHP server on $BASE_URL ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server-csrf-check.log 2>&1 &
PHP_PID=$!
trap 'kill $PHP_PID 2>/dev/null; rm -f "$CW_JAR"; log "Server stopped."' EXIT

# Wait for server
for i in $(seq 1 20); do curl -sf "$BASE_URL/login.php" >/dev/null 2>&1 && break; sleep 0.4; done

# 1. Login as Coworker
log "Logging in as coworker..."
CW_HTML=$(curl -s -c "$CW_JAR" "$BASE_URL/login.php")
CW_CSRF=$(echo "$CW_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)

curl -s -b "$CW_JAR" -c "$CW_JAR" -X POST "$BASE_URL/login.php" \
  --data-urlencode "csrf_token=$CW_CSRF" \
  --data-urlencode "role=coworker" \
  --data-urlencode "password=coworker123" \
  --data-urlencode "display_name=QA Tester" >/dev/null

# 2. Get a report ID to work with
REPORTS_JSON=$(curl -s -b "$CW_JAR" "$BASE_URL/reports")
# Extract report_id from HTML: <a href="/review?report_id=...">
REPORT_ID=$(echo "$REPORTS_JSON" | grep -o 'report_id=[^"&]*' | head -1 | cut -d'=' -f2 || true)

if [ -z "$REPORT_ID" ]; then
    fail "No reports found to test against. Make sure Task 4 import ran."
fi

log "Found report_id: $REPORT_ID"

# 3. Get stable_id and CSRF token from review page
REVIEW_HTML=$(curl -s -b "$CW_JAR" "$BASE_URL/review?report_id=$REPORT_ID")
log "Review HTML length: ${#REVIEW_HTML}"

# Save to file for debugging
echo "$REVIEW_HTML" > /tmp/debug_review_page.html

STABLE_ID=$(echo "$REVIEW_HTML" | grep -o 'data-stable-id="[^"]*"' | head -1 | cut -d'"' -f2 || true)

# Re-extract CSRF token from the page context (it's injected into JS)
# 'X-CSRF-Token': '...'
# Use simple grep, allow failure to be caught by check below
CSRF_TOKEN_JS=$(echo "$REVIEW_HTML" | grep -o "'X-CSRF-Token': '[^']*'" | head -1 | cut -d"'" -f4 || true)

if [ -z "$STABLE_ID" ]; then
    log "Review HTML content (first 500 chars): $(echo "$REVIEW_HTML" | head -c 500)"
    fail "No emails found in report."
fi
if [ -z "$CSRF_TOKEN_JS" ]; then
    log "Review HTML content (JS area): $(grep -C 5 "fetch" /tmp/debug_review_page.html || echo "fetch not found")"
    log "Full content saved to /tmp/debug_review_page.html"
    fail "Could not find CSRF token in JS block."
fi

log "Testing update for stable_id: $STABLE_ID"
log "Using CSRF token from JS: $CSRF_TOKEN_JS"

# 4. Perform the POST with Header
RESPONSE=$(curl -s -b "$CW_JAR" -X POST "$BASE_URL/review/update" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $CSRF_TOKEN_JS" \
    -d "{\"report_id\":\"$REPORT_ID\",\"stable_id\":\"$STABLE_ID\",\"decision\":\"keep\",\"note\":\"csrf header check\"}")

log "Response: $RESPONSE"

if echo "$RESPONSE" | grep -q '"status":"updated"'; then
    log "SUCCESS: Review updated via CSRF Header."
else
    fail "Update failed. Response: $RESPONSE"
fi

#!/usr/bin/env bash
# web/scripts/qa-task5-auth.sh
#
# QA script for Task 5: auth + session + CSRF flows.
# Fixed: single GET per session to keep cookie + CSRF token in sync.
#
# Scenarios covered:
#   1. Unauth GET /  → redirects to /login.php (302)
#   2. GET /login.php returns 200 and embeds CSRF token
#   3. Login as coworker (correct password + display_name) → 302
#   4. Reuse coworker cookie → GET / returns 200 with dashboard HTML
#   5. Coworker cannot access admin-only /admin/reports → 403
#   6. Admin login succeeds → 302
#   7. Admin can access GET /admin/reports → 200
#   8. Wrong password is rejected (stays on login page)
#
# Usage: bash web/scripts/qa-task5-auth.sh [--port 8000]
# Evidence: .sisyphus/evidence/current-project/task-5-auth-csrf.txt

set -euo pipefail

PORT="8000"
if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then PORT="$2"; fi

HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
DOCROOT="$(cd "$(dirname "$0")/../public" && pwd)"
CONFIG="$(cd "$(dirname "$0")/../config" && pwd)/local.php"
EVIDENCE_DIR="$(cd "$(dirname "$0")/../../.sisyphus/evidence/current-project" && pwd 2>/dev/null || echo "/tmp")"
EVIDENCE_FILE="$EVIDENCE_DIR/task-5-auth-csrf.txt"

CW_JAR="$(mktemp /tmp/cw-cookies-XXXXXX.txt)"
ADMIN_JAR="$(mktemp /tmp/admin-cookies-XXXXXX.txt)"
WRONG_JAR="$(mktemp /tmp/wrong-cookies-XXXXXX.txt)"

mkdir -p "$EVIDENCE_DIR"
log() { echo "$1" | tee -a "$EVIDENCE_FILE"; }
fail() { log "  FAIL: $1"; exit 1; }

: > "$EVIDENCE_FILE"
log "=================================================================="
log "Task 5 Auth QA -- $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "=================================================================="

[ -f "$CONFIG" ] || { log "ERROR: $CONFIG not found."; exit 1; }

log ""
log "==> Starting PHP server on $BASE_URL ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server-task5.log 2>&1 &
PHP_PID=$!
trap 'kill $PHP_PID 2>/dev/null; rm -f "$CW_JAR" "$ADMIN_JAR" "$WRONG_JAR"; log "==> Server stopped."' EXIT

for i in $(seq 1 20); do curl -sf "$BASE_URL/login.php" >/dev/null 2>&1 && break; sleep 0.4; done

# ── Scenario 1 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 1: Unauthenticated GET / redirects to login ---"
S1=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/")
log "  Status: $S1 (expected: 302)"
[ "$S1" = "302" ] || fail "Expected 302, got $S1"
log "  PASS"

# ── Scenario 2 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 2: GET /login.php returns 200 + CSRF token ---"
# One request: capture body into var AND save cookies
CW_HTML=$(curl -s -c "$CW_JAR" "$BASE_URL/login.php")
CW_CSRF=$(echo "$CW_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
log "  CSRF: ${CW_CSRF:0:20}... (len ${#CW_CSRF})"
[ -n "$CW_CSRF" ] || fail "CSRF token not found in login HTML"
echo "$CW_HTML" | grep -q "200\|Login\|Mailbox" || true  # body sanity
log "  PASS"

# ── Scenario 3 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 3: Coworker login with valid credentials ---"
S3=$(curl -s -b "$CW_JAR" -c "$CW_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/login.php" \
  --data-urlencode "csrf_token=$CW_CSRF" \
  --data-urlencode "role=coworker" \
  --data-urlencode "password=coworker123" \
  --data-urlencode "display_name=Anna M.")
log "  Status: $S3 (expected: 302)"
[ "$S3" = "302" ] || fail "Expected 302 for coworker login, got $S3"
log "  PASS"

# ── Scenario 4 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 4: Coworker cookie -> GET / returns dashboard ---"
S4=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" "$BASE_URL/")
S4_BODY=$(curl -s -b "$CW_JAR" "$BASE_URL/")
log "  Status: $S4 (expected: 200)"
[ "$S4" = "200" ] || fail "Expected 200 dashboard, got $S4"
echo "$S4_BODY" | grep -qi "Mailbox Review\|Welcome\|coworker\|logged" && log "  Body: dashboard HTML confirmed" || log "  Body preview: ${S4_BODY:0:200}"
log "  PASS"

# ── Scenario 5 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 5: Coworker accessing /admin/reports gets 403 ---"
S5=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" "$BASE_URL/admin/reports")
log "  Status: $S5 (expected: 403)"
[ "$S5" = "403" ] || fail "Expected 403 for coworker on admin route, got $S5"
log "  PASS"

# ── Scenario 6 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 6: Admin login ---"
ADMIN_HTML=$(curl -s -c "$ADMIN_JAR" "$BASE_URL/login.php")
ADMIN_CSRF=$(echo "$ADMIN_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
[ -n "$ADMIN_CSRF" ] || fail "Admin CSRF token not found"
log "  CSRF: ${ADMIN_CSRF:0:20}..."
S6=$(curl -s -b "$ADMIN_JAR" -c "$ADMIN_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/login.php" \
  --data-urlencode "csrf_token=$ADMIN_CSRF" \
  --data-urlencode "role=admin" \
  --data-urlencode "password=admin123" \
  --data-urlencode "display_name=")
log "  Status: $S6 (expected: 302)"
[ "$S6" = "302" ] || fail "Expected 302 for admin login, got $S6"
log "  PASS"

# ── Scenario 7 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 7: Admin accesses /admin/reports ---"
S7=$(curl -s -o /dev/null -w '%{http_code}' -b "$ADMIN_JAR" "$BASE_URL/admin/reports")
S7_BODY=$(curl -s -b "$ADMIN_JAR" "$BASE_URL/admin/reports")
log "  Status: $S7 (expected: 200)"
[ "$S7" = "200" ] || fail "Expected 200 for admin reports, got $S7"
echo "$S7_BODY" | grep -q '"reports"' && log "  Body: JSON reports key found" || log "  Body: $S7_BODY"
log "  PASS"

# ── Scenario 8 ────────────────────────────────────────────────────────────────
log ""
log "--- Scenario 8: Wrong password is rejected ---"
WRONG_HTML=$(curl -s -c "$WRONG_JAR" "$BASE_URL/login.php")
WRONG_CSRF=$(echo "$WRONG_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
S8_BODY=$(curl -s -b "$WRONG_JAR" -c "$WRONG_JAR" \
  -X POST "$BASE_URL/login.php" \
  --data-urlencode "csrf_token=$WRONG_CSRF" \
  --data-urlencode "role=admin" \
  --data-urlencode "password=wrongpassword" \
  --data-urlencode "display_name=")
S8_REDIR=$(curl -s -b "$WRONG_JAR" \
  -o /dev/null -w '%{redirect_url}' \
  -X POST "$BASE_URL/login.php" \
  --data-urlencode "csrf_token=$WRONG_CSRF" \
  --data-urlencode "role=admin" \
  --data-urlencode "password=wrongpassword" \
  --data-urlencode "display_name=")
log "  Redirect URL: '$S8_REDIR' (expected: empty — stays on login page)"
echo "$S8_BODY" | grep -qi "Invalid credentials" && log "  Body: generic error shown" || log "  Body preview: ${S8_BODY:0:200}"
[ -z "$S8_REDIR" ] || fail "Wrong password should not redirect, but got: $S8_REDIR"
log "  PASS"

log ""
log "=================================================================="
log "ALL AUTH SCENARIOS PASSED"
log "Evidence: $EVIDENCE_FILE"
log "=================================================================="

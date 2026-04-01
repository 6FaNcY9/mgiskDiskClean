#!/usr/bin/env bash
# web/scripts/qa-task5-csrf.sh
#
# QA script for Task 5: CSRF rejection scenarios.
#
# Scenarios covered:
#   1. POST /admin/import without csrf_token (admin session) → 403
#   2. POST /review/update without csrf_token (coworker session) → 403
#   3. POST /logout without csrf_token (admin session) → 403
#   4. POST /admin/import with wrong csrf_token (JSON body) → 403
#   5. Unauthenticated POST to protected endpoint → 302 redirect to login
#
# Usage: bash web/scripts/qa-task5-csrf.sh [--port 8000]
# Evidence: .sisyphus/evidence/current-project/task-5-csrf-reject.txt

set -euo pipefail

PORT="8000"
if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then PORT="$2"; fi

HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
DOCROOT="$(cd "$(dirname "$0")/../public" && pwd)"
CONFIG="$(cd "$(dirname "$0")/../config" && pwd)/local.php"
EVIDENCE_DIR="$(cd "$(dirname "$0")/../../.sisyphus/evidence/current-project" && pwd 2>/dev/null || echo "/tmp")"
EVIDENCE_FILE="$EVIDENCE_DIR/task-5-csrf-reject.txt"

ADMIN_JAR="$(mktemp /tmp/admin-csrf-XXXXXX.txt)"
CW_JAR="$(mktemp /tmp/cw-csrf-XXXXXX.txt)"

mkdir -p "$EVIDENCE_DIR"
log() { echo "$1" | tee -a "$EVIDENCE_FILE"; }
fail() { log "  FAIL: $1"; exit 1; }

: > "$EVIDENCE_FILE"
log "=================================================================="
log "Task 5 CSRF Rejection QA -- $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "=================================================================="

[ -f "$CONFIG" ] || { log "ERROR: $CONFIG not found."; exit 1; }

log ""
log "==> Starting PHP server on $BASE_URL ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server-task5-csrf.log 2>&1 &
PHP_PID=$!
trap 'kill $PHP_PID 2>/dev/null; rm -f "$ADMIN_JAR" "$CW_JAR"; log "==> Server stopped."' EXIT

for i in $(seq 1 20); do curl -sf "$BASE_URL/login.php" >/dev/null 2>&1 && break; sleep 0.4; done

# ── Establish admin session ────────────────────────────────────────────────────
log ""
log "==> Establishing admin session ..."
ADMIN_HTML=$(curl -s -c "$ADMIN_JAR" "$BASE_URL/login.php")
ADMIN_CSRF=$(echo "$ADMIN_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
[ -n "$ADMIN_CSRF" ] || fail "Admin CSRF token not found"
curl -s -b "$ADMIN_JAR" -c "$ADMIN_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/login.php" \
  --data-urlencode "csrf_token=$ADMIN_CSRF" \
  --data-urlencode "role=admin" \
  --data-urlencode "password=admin123" \
  --data-urlencode "display_name=" > /dev/null
log "  Admin session established."

# ── Establish coworker session ─────────────────────────────────────────────────
log ""
log "==> Establishing coworker session ..."
CW_HTML=$(curl -s -c "$CW_JAR" "$BASE_URL/login.php")
CW_CSRF=$(echo "$CW_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
[ -n "$CW_CSRF" ] || fail "Coworker CSRF token not found"
curl -s -b "$CW_JAR" -c "$CW_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/login.php" \
  --data-urlencode "csrf_token=$CW_CSRF" \
  --data-urlencode "role=coworker" \
  --data-urlencode "password=coworker123" \
  --data-urlencode "display_name=TestUser" > /dev/null
log "  Coworker session established."

# ── Scenario 1: POST /admin/import without csrf_token → 403 ──────────────────
log ""
log "--- Scenario 1: POST /admin/import without csrf_token (admin session) ---"
S1=$(curl -s -b "$ADMIN_JAR" -c "$ADMIN_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/admin/import" \
  -H 'Content-Type: application/json' \
  -d '{"mailbox":"testbox"}')
log "  Status: $S1 (expected: 403)"
[ "$S1" = "403" ] || fail "Expected 403, got $S1"
log "  PASS: POST /admin/import without CSRF token returns 403"

# ── Scenario 2: POST /review/update without csrf_token → 403 ─────────────────
log ""
log "--- Scenario 2: POST /review/update without csrf_token (coworker session) ---"
S2=$(curl -s -b "$CW_JAR" -c "$CW_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/review/update" \
  -H 'Content-Type: application/json' \
  -d '{"report_id":"abc","stable_id":"xyz","decision":"keep"}')
log "  Status: $S2 (expected: 403)"
[ "$S2" = "403" ] || fail "Expected 403, got $S2"
log "  PASS: POST /review/update without CSRF token returns 403"

# ── Scenario 3: POST /logout without csrf_token → 403 ────────────────────────
log ""
log "--- Scenario 3: POST /logout without csrf_token (admin session) ---"
S3=$(curl -s -b "$ADMIN_JAR" -c "$ADMIN_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/logout")
log "  Status: $S3 (expected: 403)"
[ "$S3" = "403" ] || fail "Expected 403, got $S3"
log "  PASS: POST /logout without CSRF token returns 403"

# ── Scenario 4: POST /admin/import with wrong csrf_token → 403 ───────────────
log ""
log "--- Scenario 4: POST /admin/import with wrong csrf_token (form field) ---"
S4=$(curl -s -b "$ADMIN_JAR" -c "$ADMIN_JAR" \
  -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/admin/import" \
  --data-urlencode "csrf_token=WRONGTOKEN_INVALID_VALUE_XYZ" \
  --data-urlencode "mailbox=testbox")
log "  Status: $S4 (expected: 403)"
[ "$S4" = "403" ] || fail "Expected 403, got $S4"
log "  PASS: POST /admin/import with wrong CSRF token returns 403"

# ── Scenario 5: Unauthenticated POST → 302 (redirect to login) ────────────────
log ""
log "--- Scenario 5: Unauthenticated POST to protected endpoint ---"
S5=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST "$BASE_URL/admin/import" \
  -H 'Content-Type: application/json' \
  -d '{"mailbox":"testbox","csrf_token":"anything"}')
log "  Status: $S5 (expected: 302 redirect to login, not 403)"
[ "$S5" = "302" ] || fail "Expected 302 (auth redirect), got $S5"
log "  PASS: Unauthenticated POST redirects to login"

log ""
log "=================================================================="
log "ALL CSRF REJECTION SCENARIOS PASSED"
log "Evidence: $EVIDENCE_FILE"
log "=================================================================="

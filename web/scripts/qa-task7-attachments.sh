#!/usr/bin/env bash
# web/scripts/qa-task7-attachments.sh
# QA script for Task 7: authenticated attachment download route.
#
# Tests:
#   1. Unauthenticated request -> 302 redirect to login
#   2. Coworker authenticated + valid params -> 200 with file content
#   3. Path traversal attempts in sha256 param -> 404 (route regex guard)
#   4. Mismatched report_id -> 404
#   5. Unknown sha256 (valid format, not in index) -> 404
#
# Must be run inside devenv shell:  devenv shell -- bash web/scripts/qa-task7-attachments.sh

set -euo pipefail

PORT="8099"
if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then PORT="$2"; fi

HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCROOT="$SCRIPT_DIR/../public"

EVIDENCE_BASE="$SCRIPT_DIR/../../.sisyphus/evidence/current-project"
mkdir -p "$EVIDENCE_BASE"
EVIDENCE_DIR="$(cd "$EVIDENCE_BASE" && pwd)"
EVIDENCE_FILE="$EVIDENCE_DIR/task-7-attachments.txt"
TRAVERSAL_FILE="$EVIDENCE_DIR/task-7-attachments-traversal.txt"

# Derive data_dir from env (matches web/config/local.php: getenv('DEVENV_ROOT').'/data')
if [ -z "${DEVENV_ROOT:-}" ]; then
    echo "ERROR: DEVENV_ROOT not set. Run via: devenv shell -- bash $0" >&2
    exit 1
fi
DATA_DIR="$DEVENV_ROOT/data"

TESTBOX_DIR="$DATA_DIR/mailboxes/testbox"
ATTACH_DIR="$TESTBOX_DIR/attachments"
INDEX_DB="$TESTBOX_DIR/index.sqlite"

mkdir -p "$ATTACH_DIR"

CW_JAR="$(mktemp /tmp/cw-cookies-task7-XXXXXX.txt)"
TMP_OUT="$(mktemp /tmp/qa-task7-out-XXXXXX.txt)"
PHP_PID=""

cleanup() {
    [ -n "$PHP_PID" ] && kill "$PHP_PID" 2>/dev/null || true
    rm -f "$CW_JAR" "$TMP_OUT" 2>/dev/null || true
    echo "==> Server stopped." | tee -a "$EVIDENCE_FILE"
}
trap cleanup EXIT

log()   { echo "$1" | tee -a "$EVIDENCE_FILE"; }
log_t() { echo "$1" | tee -a "$TRAVERSAL_FILE"; }
pass()  { log "  PASS: $1"; }
fail()  { log "  FAIL: $1"; exit 1; }

: > "$EVIDENCE_FILE"
: > "$TRAVERSAL_FILE"

log "=================================================================="
log "Task 7 Attachment Download QA -- $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "=================================================================="
log ""

# ── Fixture Setup ──────────────────────────────────────────────────────────
log "==> Setting up fixture data ..."

# Known report/email stable_id from MariaDB (imported testbox report)
REPORT_ID="c3d2545d5614d813f2e048ae0d4c93f0cb07ed01ad032da6d2e6b155745ad48c"
STABLE_ID="6b055e0f296ff239f745500dc013220eb9b16fb6e3a2c6b3f5e68edd18508d02"

# Tiny test attachment file with known content
ATTACH_CONTENT="hello attachment qa test"
ATTACH_SHA256="$(printf '%s' "$ATTACH_CONTENT" | sha256sum | cut -d' ' -f1)"
ATTACH_SIZE="$(printf '%s' "$ATTACH_CONTENT" | wc -c | tr -d ' ')"
ATTACH_FILENAME="test-attachment.txt"
ATTACH_STORED="$ATTACH_DIR/${ATTACH_SHA256}_${ATTACH_SIZE}.txt"

printf '%s' "$ATTACH_CONTENT" > "$ATTACH_STORED"
log "  Created attachment: $ATTACH_STORED"
log "  sha256: $ATTACH_SHA256  size: ${ATTACH_SIZE} bytes"

# Insert fixture row into per-mailbox SQLite index
php -r "
    \$db = new SQLite3('$INDEX_DB');
    \$db->enableExceptions(true);
    \$db->exec('CREATE TABLE IF NOT EXISTS attachments (
        sha256            TEXT    NOT NULL,
        size_bytes        INTEGER NOT NULL DEFAULT 0,
        mime              TEXT    NOT NULL DEFAULT \\'\\',
        original_filename TEXT    NOT NULL DEFAULT \\'\\',
        stored_path       TEXT    NOT NULL,
        email_stable_id   TEXT    NOT NULL,
        PRIMARY KEY (stored_path, email_stable_id)
    )');
    \$db->exec('CREATE INDEX IF NOT EXISTS idx_att_sha256 ON attachments (sha256)');
    \$del = \$db->prepare('DELETE FROM attachments WHERE sha256 = ? AND email_stable_id = ?');
    \$del->bindValue(1, '$ATTACH_SHA256', SQLITE3_TEXT);
    \$del->bindValue(2, '$STABLE_ID',    SQLITE3_TEXT);
    \$del->execute();
    \$ins = \$db->prepare('INSERT INTO attachments
        (sha256, size_bytes, mime, original_filename, stored_path, email_stable_id)
        VALUES (?,?,?,?,?,?)');
    \$ins->bindValue(1, '$ATTACH_SHA256',              SQLITE3_TEXT);
    \$ins->bindValue(2, (int)'$ATTACH_SIZE',           SQLITE3_INTEGER);
    \$ins->bindValue(3, 'text/plain; charset=utf-8',   SQLITE3_TEXT);
    \$ins->bindValue(4, '$ATTACH_FILENAME',            SQLITE3_TEXT);
    \$ins->bindValue(5, '$ATTACH_STORED',              SQLITE3_TEXT);
    \$ins->bindValue(6, '$STABLE_ID',                  SQLITE3_TEXT);
    \$ins->execute();
    \$db->close();
    echo 'ok';
"
log "  SQLite fixture ready (index.sqlite updated)."
log ""

# ── Start PHP server ──────────────────────────────────────────────────────
log "==> Starting PHP server on $BASE_URL ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server-task7.log 2>&1 &
PHP_PID=$!

for i in $(seq 1 25); do
    curl -sf "$BASE_URL/login.php" >/dev/null 2>&1 && break
    sleep 0.4
done
log "  PHP server running (PID $PHP_PID)."

# ── Login as coworker ──────────────────────────────────────────────────────
log ""
log "==> Logging in as coworker ..."
CW_HTML=$(curl -s -c "$CW_JAR" "$BASE_URL/login.php")
CW_CSRF=$(echo "$CW_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
curl -s -b "$CW_JAR" -c "$CW_JAR" -X POST "$BASE_URL/login.php" \
    --data-urlencode "csrf_token=$CW_CSRF" \
    --data-urlencode "role=coworker" \
    --data-urlencode "password=coworker123" \
    --data-urlencode "display_name=QA Tester" >/dev/null
log "  Logged in as coworker."

ATTACH_URL="$BASE_URL/download/attachment/$REPORT_ID/$STABLE_ID/$ATTACH_SHA256"

# ── Test 1: Unauthenticated access -> redirect ──────────────────────────────
log ""
log "--- Test 1: Unauthenticated request (no auth cookie) ---"
STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$ATTACH_URL")
log "  GET (no cookie): HTTP $STATUS  (expected: 302)"
if [ "$STATUS" = "302" ] || [ "$STATUS" = "401" ]; then
    pass "Unauthenticated request redirected/rejected (HTTP $STATUS)"
else
    fail "Expected 302 or 401 for unauthenticated request, got $STATUS"
fi

# ── Test 2: Authenticated valid download -> 200 ────────────────────────────
log ""
log "--- Test 2: Authenticated coworker — valid attachment download ---"
HTTP_CODE=$(curl -s -o "$TMP_OUT" -w '%{http_code}' -b "$CW_JAR" "$ATTACH_URL")
BODY="$(cat "$TMP_OUT")"
BODY_LEN="${#BODY}"
log "  GET (auth) $ATTACH_URL"
log "  HTTP response: $HTTP_CODE  (expected: 200)"
log "  Content received: $BODY_LEN bytes"
if [ "$HTTP_CODE" = "200" ] && [ "$BODY_LEN" -gt 0 ]; then
    pass "Attachment downloaded successfully (HTTP 200, $BODY_LEN bytes)"
else
    log "  Body: $BODY"
    fail "Expected HTTP 200 with content, got $HTTP_CODE (len=$BODY_LEN)"
fi

# ── Test 3: Path traversal attempts ────────────────────────────────────────
log ""
log "--- Test 3: Path traversal attempts (sha256 position) ---"
log_t "=================================================================="
log_t "Task 7 Path Traversal Rejection Proof"
log_t "Date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log_t "=================================================================="
log_t ""
log_t "Route pattern in web/public/index.php:"
log_t "  preg_match('#^/download/attachment/([^/]+)/([^/]+)/([0-9a-fA-F]{64})\$#', \$path, \$am)"
log_t ""
log_t "The sha256 capture group requires EXACTLY 64 chars from [0-9a-fA-F]."
log_t "Any path traversal char (., /, \\, %, null) is rejected at route level."
log_t "stored_path is additionally guarded by DownloadService::assertPathUnderRoot()."
log_t ""

# Attempt 1: raw ../ traversal string (not hex, route regex won't match -> 404)
T1_URL="$BASE_URL/download/attachment/$REPORT_ID/$STABLE_ID/../../../../etc/passwd"
S1=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" "$T1_URL")
log "  [1] ../ traversal: HTTP $S1"
log_t "Attempt 1: sha256='../../../../etc/passwd'"
log_t "  HTTP response: $S1"
if [ "$S1" = "404" ] || [ "$S1" = "400" ]; then
    pass "Raw traversal (../): HTTP $S1 — REJECTED"
    log_t "  RESULT: PASS (HTTP $S1)"
else
    log_t "  RESULT: FAIL (HTTP $S1)"
    fail "Raw traversal ../ should be rejected, got $S1"
fi
log_t ""

# Attempt 2: URL-encoded ../ traversal
T2_URL="$BASE_URL/download/attachment/$REPORT_ID/$STABLE_ID/%2E%2E%2F%2E%2E%2Fetc%2Fpasswd"
S2=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" -g "$T2_URL")
log "  [2] URL-encoded ../: HTTP $S2"
log_t "Attempt 2: sha256='%2E%2E%2F%2E%2E%2Fetc%2Fpasswd' (URL-encoded)"
log_t "  HTTP response: $S2"
if [ "$S2" = "404" ] || [ "$S2" = "400" ]; then
    pass "URL-encoded traversal: HTTP $S2 — REJECTED"
    log_t "  RESULT: PASS (HTTP $S2)"
else
    log_t "  RESULT: FAIL (HTTP $S2)"
    fail "URL-encoded traversal should be rejected, got $S2"
fi
log_t ""

# Attempt 3: 64-char string with non-hex char 'g' (invalid hex -> route regex no-match)
FAKE_HEX="000000000000000000000000000000000000000000000000000000000000000g"
T3_URL="$BASE_URL/download/attachment/$REPORT_ID/$STABLE_ID/$FAKE_HEX"
S3=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" "$T3_URL")
log "  [3] Non-hex 64-char sha256: HTTP $S3"
log_t "Attempt 3: sha256='$FAKE_HEX' (64 chars, 'g' is not in [0-9a-fA-F])"
log_t "  HTTP response: $S3"
if [ "$S3" = "404" ] || [ "$S3" = "400" ]; then
    pass "Non-hex 64-char sha256: HTTP $S3 — REJECTED"
    log_t "  RESULT: PASS (HTTP $S3)"
else
    log_t "  RESULT: FAIL (HTTP $S3)"
    fail "Non-hex sha256 should be rejected, got $S3"
fi
log_t ""

# ── Test 4: Mismatched report_id -> 404 ────────────────────────────────────
log ""
log "--- Test 4: Mismatched report_id ---"
T4_URL="$BASE_URL/download/attachment/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/$STABLE_ID/$ATTACH_SHA256"
S4=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" "$T4_URL")
log "  GET (report_id='aaaa...64'): HTTP $S4  (expected: 404)"
if [ "$S4" = "404" ] || [ "$S4" = "400" ]; then
    pass "Mismatched report_id: HTTP $S4 — REJECTED"
else
    fail "Expected 404 for mismatched report_id, got $S4"
fi

# ── Test 5: Unknown sha256 (valid hex, not in index) -> 404 ────────────────
log ""
log "--- Test 5: Unknown sha256 (valid hex format, not in index) ---"
T5_URL="$BASE_URL/download/attachment/$REPORT_ID/$STABLE_ID/1111111111111111111111111111111111111111111111111111111111111111"
S5=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" "$T5_URL")
log "  GET (sha256='1111...64'): HTTP $S5  (expected: 404)"
if [ "$S5" = "404" ]; then
    pass "Unknown sha256 returns HTTP 404"
else
    fail "Expected 404 for unknown sha256, got $S5"
fi

# ── Summary ────────────────────────────────────────────────────────────────
log ""
log "=================================================================="
log "ALL 5 TESTS PASSED"
log "=================================================================="

log_t "=================================================================="
log_t "Summary: All path traversal attempts rejected."
log_t "Security layers:"
log_t "  1. Route regex: [0-9a-fA-F]{64} — rejects non-hex / short strings"
log_t "  2. DownloadService::assertPathUnderRoot() — verifies stored_path"
log_t "     is under data_dir/mailboxes/<mailbox>/attachments/"
log_t "  3. MariaDB scope check: report_id must own the email_stable_id"
log_t "=================================================================="

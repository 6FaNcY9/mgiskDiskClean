#!/usr/bin/env bash
# web/scripts/qa-task9-export.sh
# QA script for Task 9: admin-only CSV export endpoints.
#
# Tests:
#   1. GET /admin/export/decisions?report_id=testreport9
#      -> HTTP 200, correct CSV header, 3 data rows, decision values correct
#   2. Parse the CSV in Python and verify columns + row count + decision values.
#   3. GET /admin/export/audit?report_id=testreport9
#      -> HTTP 200, audit CSV has extra columns (updated_by, updated_at)
#   4. GET /admin/export/decisions?report_id=no-such-report -> HTTP 404
#   5. Non-admin (coworker) cannot access export -> HTTP 302/403
#
# Must be run inside devenv shell:  devenv shell -- bash web/scripts/qa-task9-export.sh

set -euo pipefail

PORT="8109"
if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then PORT="$2"; fi

HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCROOT="$SCRIPT_DIR/../public"

EVIDENCE_BASE="$SCRIPT_DIR/../../.sisyphus/evidence/current-project"
mkdir -p "$EVIDENCE_BASE"
EVIDENCE_DIR="$(cd "$EVIDENCE_BASE" && pwd)"
EVIDENCE_FILE="$EVIDENCE_DIR/task-9-export.txt"
EVIDENCE_404_FILE="$EVIDENCE_DIR/task-9-export-404.txt"

if [ -z "${DEVENV_ROOT:-}" ]; then
    echo "ERROR: DEVENV_ROOT not set. Run via: devenv shell -- bash $0" >&2
    exit 1
fi

SOCK="${DEVENV_STATE:-}/mysql.sock"

FAIL_COUNT=0
ADMIN_JAR="$(mktemp /tmp/admin-cookies-task9-XXXXXX.txt)"
CW_JAR="$(mktemp /tmp/cw-cookies-task9-XXXXXX.txt)"
TMP_CSV="$(mktemp /tmp/qa-task9-csv-XXXXXX.csv)"
TMP_AUDIT_CSV="$(mktemp /tmp/qa-task9-audit-XXXXXX.csv)"
PHP_PID=""

cleanup() {
    [ -n "$PHP_PID" ] && kill "$PHP_PID" 2>/dev/null || true
    rm -f "$ADMIN_JAR" "$CW_JAR" "$TMP_CSV" "$TMP_AUDIT_CSV" 2>/dev/null || true
    echo "==> Server stopped." | tee -a "$EVIDENCE_FILE"
}
trap cleanup EXIT

log()  { echo "$1" | tee -a "$EVIDENCE_FILE"; }
log4() { echo "$1" | tee -a "$EVIDENCE_404_FILE"; }
pass() { log "  PASS: $1"; }
fail() { log "  FAIL: $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

: > "$EVIDENCE_FILE"
: > "$EVIDENCE_404_FILE"

log "=================================================================="
log "Task 9 Export CSV QA -- $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "=================================================================="
log ""

log4 "=================================================================="
log4 "Task 9 Export 404 QA -- $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log4 "=================================================================="
log4 ""

# ── Fixture: insert report + emails + decisions into MariaDB ────────────────
log "==> Setting up fixture data in MariaDB ..."

REPORT_ID="testreport9"

mysql -u mailreview --socket="$SOCK" mailreview <<'SQL'
-- Idempotent fixture setup for Task 9 QA
DELETE FROM decisions WHERE report_id = 'testreport9';
DELETE FROM emails    WHERE report_id = 'testreport9';
DELETE FROM reports   WHERE report_id = 'testreport9';

INSERT INTO reports (report_id, mailbox, generated_at, pdf_path, manifest_path, decisions_seed_path)
VALUES ('testreport9', 'testbox9', '2026-01-01T00:00:00Z', '', '', '');

INSERT INTO emails (report_id, stable_id, folder, date, sender, subject, total_size_bytes, is_duplicate, dup_group_id, dup_rank)
VALUES
  ('testreport9', 'e9-stable-1', 'INBOX', '2026-01-01', 'alice@test.com', 'Subject Alpha', 1024, 0, '',       -1),
  ('testreport9', 'e9-stable-2', 'INBOX', '2026-01-02', 'bob@test.com',   'Subject Beta',  2048, 1, 'grp1',   0),
  ('testreport9', 'e9-stable-3', 'INBOX', '2026-01-03', 'carol@test.com', 'Subject Gamma', 512,  1, 'grp1',   1);

INSERT INTO decisions (report_id, stable_id, decision, note, updated_at, updated_by)
VALUES
  ('testreport9', 'e9-stable-1', 'keep',   'important', NOW(), 'admin'),
  ('testreport9', 'e9-stable-2', 'delete', 'duplicate', NOW(), 'admin'),
  ('testreport9', 'e9-stable-3', 'delete', 'dup copy',  NOW(), 'admin');
SQL

log "  Fixture inserted: report testreport9, 3 emails (1 keep, 2 delete)."
log ""

# ── Start PHP server ─────────────────────────────────────────────────────────
log "==> Starting PHP server on $BASE_URL ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server-task9.log 2>&1 &
PHP_PID=$!

for i in $(seq 1 25); do
    curl -sf "$BASE_URL/login.php" >/dev/null 2>&1 && break
    sleep 0.4
done
log "  PHP server running (PID $PHP_PID)."

# ── Login as admin ────────────────────────────────────────────────────────────
log ""
log "==> Logging in as admin ..."
ADMIN_HTML=$(curl -s -c "$ADMIN_JAR" "$BASE_URL/login.php")
ADMIN_CSRF=$(echo "$ADMIN_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
curl -s -b "$ADMIN_JAR" -c "$ADMIN_JAR" -X POST "$BASE_URL/login.php" \
    --data-urlencode "csrf_token=$ADMIN_CSRF" \
    --data-urlencode "role=admin" \
    --data-urlencode "password=admin123" \
    --data-urlencode "display_name=Admin QA9" >/dev/null
log "  Logged in as admin."

# ── Login as coworker (for access control test) ───────────────────────────────
log ""
log "==> Logging in as coworker (for access-control test) ..."
CW_HTML=$(curl -s -c "$CW_JAR" "$BASE_URL/login.php")
CW_CSRF=$(echo "$CW_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
curl -s -b "$CW_JAR" -c "$CW_JAR" -X POST "$BASE_URL/login.php" \
    --data-urlencode "csrf_token=$CW_CSRF" \
    --data-urlencode "role=coworker" \
    --data-urlencode "password=coworker123" \
    --data-urlencode "display_name=CW QA9" >/dev/null
log "  Logged in as coworker."

# ── Test 1: GET /admin/export/decisions -> HTTP 200 + valid CSV ──────────────
log ""
log "--- Test 1: GET /admin/export/decisions?report_id=testreport9 ---"

HTTP_STATUS=$(curl -s -o "$TMP_CSV" -w '%{http_code}' -b "$ADMIN_JAR" \
    "$BASE_URL/admin/export/decisions?report_id=$REPORT_ID")

log "  HTTP status: $HTTP_STATUS (expected 200)"
log "  CSV content:"
log "---"
while IFS= read -r line; do log "  $line"; done < "$TMP_CSV"
log "---"

if [ "$HTTP_STATUS" = "200" ]; then
    pass "decisions export HTTP 200"
else
    fail "decisions export expected 200, got $HTTP_STATUS"
fi

# ── Test 2: Parse CSV in Python ───────────────────────────────────────────────
log ""
log "--- Test 2: Parse decisions.reviewed.csv in Python ---"

PYTHON_OUT=$(python3 - <<PYEOF
import csv, sys

EXPECTED_HEADER = [
    'stable_id','date','sender','subject','size_bytes',
    'has_attachments','attachment_count','attachment_total_bytes',
    'attachment_extensions','dup_group_id','dup_rank','total_size_bytes',
    'decision','note',
]

with open('$TMP_CSV', newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

actual_header = reader.fieldnames or []
errors = []

if actual_header != EXPECTED_HEADER:
    errors.append(f"Header mismatch.\n  Got:      {actual_header}\n  Expected: {EXPECTED_HEADER}")

if len(rows) != 3:
    errors.append(f"Expected 3 data rows, got {len(rows)}")

decisions = [r['decision'] for r in rows]
keep_count   = decisions.count('keep')
delete_count = decisions.count('delete')
if keep_count != 1:
    errors.append(f"Expected 1 keep, got {keep_count}")
if delete_count != 2:
    errors.append(f"Expected 2 delete, got {delete_count}")

# Verify ordering: date ASC, stable_id ASC
dates = [r['date'] for r in rows]
if dates != sorted(dates):
    errors.append(f"Rows not ordered by date ASC: {dates}")

if errors:
    print("PYTHON_FAIL: " + "; ".join(errors))
else:
    print(f"PYTHON_PASS: header OK, {len(rows)} rows, keep={keep_count} delete={delete_count}, ordering OK")
PYEOF
)

log "  Python result: $PYTHON_OUT"
if echo "$PYTHON_OUT" | grep -q "PYTHON_PASS"; then
    pass "Python CSV parse: header + row count + decisions + ordering correct"
else
    fail "Python CSV parse failed: $PYTHON_OUT"
fi

# ── Test 3: GET /admin/export/audit -> HTTP 200 + extra columns ──────────────
log ""
log "--- Test 3: GET /admin/export/audit?report_id=testreport9 ---"

AUDIT_STATUS=$(curl -s -o "$TMP_AUDIT_CSV" -w '%{http_code}' -b "$ADMIN_JAR" \
    "$BASE_URL/admin/export/audit?report_id=$REPORT_ID")

log "  HTTP status: $AUDIT_STATUS (expected 200)"
log "  Audit CSV header:"
AUDIT_HEADER=$(head -1 "$TMP_AUDIT_CSV")
log "  $AUDIT_HEADER"

if [ "$AUDIT_STATUS" = "200" ]; then
    pass "audit export HTTP 200"
else
    fail "audit export expected 200, got $AUDIT_STATUS"
fi

AUDIT_PYTHON_OUT=$(python3 - <<PYEOF
import csv

EXPECTED_HEADER = [
    'stable_id','date','sender','subject','size_bytes',
    'has_attachments','attachment_count','attachment_total_bytes',
    'attachment_extensions','dup_group_id','dup_rank','total_size_bytes',
    'decision','note','updated_by','updated_at',
]

with open('$TMP_AUDIT_CSV', newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

actual_header = reader.fieldnames or []
errors = []

if actual_header != EXPECTED_HEADER:
    errors.append(f"Audit header mismatch.\n  Got:      {actual_header}\n  Expected: {EXPECTED_HEADER}")

if len(rows) != 3:
    errors.append(f"Expected 3 audit rows, got {len(rows)}")

# Check updated_by is populated
populated = sum(1 for r in rows if r.get('updated_by','') != '')
if populated < 1:
    errors.append(f"Expected at least 1 row with updated_by set, got {populated}")

if errors:
    print("PYTHON_FAIL: " + "; ".join(errors))
else:
    print(f"PYTHON_PASS: audit header OK, {len(rows)} rows, {populated} with updated_by set")
PYEOF
)
log "  Audit Python result: $AUDIT_PYTHON_OUT"
if echo "$AUDIT_PYTHON_OUT" | grep -q "PYTHON_PASS"; then
    pass "Audit CSV parse: header + row count + updated_by populated"
else
    fail "Audit CSV parse failed: $AUDIT_PYTHON_OUT"
fi

# ── Test 4: Fake report_id -> HTTP 404 ───────────────────────────────────────
log ""
log "--- Test 4: fake report_id -> expect HTTP 404 ---"
log4 ""
log4 "--- Test 4: GET /admin/export/decisions?report_id=no-such-report ---"

STATUS_404=$(curl -s -o /tmp/qa-task9-404-body.txt -w '%{http_code}' -b "$ADMIN_JAR" \
    "$BASE_URL/admin/export/decisions?report_id=no-such-report")
BODY_404=$(cat /tmp/qa-task9-404-body.txt 2>/dev/null || echo "")

log "  HTTP status: $STATUS_404 (expected 404)"
log "  Body: $BODY_404"
log4 "URL: $BASE_URL/admin/export/decisions?report_id=no-such-report"
log4 "HTTP status: $STATUS_404 (expected 404)"
log4 "Body: $BODY_404"

if [ "$STATUS_404" = "404" ]; then
    pass "Fake report_id -> HTTP 404"
    log4 "PASS: fake report_id correctly returns 404"
else
    fail "Expected 404 for fake report_id, got $STATUS_404"
    log4 "FAIL: expected 404, got $STATUS_404"
fi

# Also test audit 404
STATUS_AUDIT_404=$(curl -s -o /dev/null -w '%{http_code}' -b "$ADMIN_JAR" \
    "$BASE_URL/admin/export/audit?report_id=no-such-report")
log4 ""
log4 "URL: $BASE_URL/admin/export/audit?report_id=no-such-report"
log4 "HTTP status: $STATUS_AUDIT_404 (expected 404)"
if [ "$STATUS_AUDIT_404" = "404" ]; then
    pass "Fake report_id on audit -> HTTP 404"
    log4 "PASS: audit 404 for fake report_id"
else
    fail "Audit: expected 404 for fake report_id, got $STATUS_AUDIT_404"
    log4 "FAIL: audit expected 404, got $STATUS_AUDIT_404"
fi
rm -f /tmp/qa-task9-404-body.txt 2>/dev/null || true

# ── Test 5: Coworker cannot access export (should redirect to login) ─────────
log ""
log "--- Test 5: coworker access to export -> expect redirect (302) or 403 ---"

CW_STATUS=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" \
    "$BASE_URL/admin/export/decisions?report_id=$REPORT_ID" \
    --max-redirs 0)

log "  HTTP status: $CW_STATUS (expected 302 or 403)"
if [ "$CW_STATUS" = "302" ] || [ "$CW_STATUS" = "403" ]; then
    pass "Coworker correctly denied access (HTTP $CW_STATUS)"
else
    fail "Expected 302/403 for coworker, got $CW_STATUS"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
log ""
log "=================================================================="
if [ "$FAIL_COUNT" -eq 0 ]; then
    log "ALL TESTS PASSED"
else
    log "FAILED: $FAIL_COUNT test(s) failed"
fi
log "=================================================================="

log4 ""
log4 "=================================================================="
log4 "Task 9 Export 404 tests complete"
log4 "=================================================================="

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi

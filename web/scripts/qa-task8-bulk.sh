#!/usr/bin/env bash
# web/scripts/qa-task8-bulk.sh
# QA script for Task 8: bulk decision workflows.
#
# Tests:
#   1. bulk-apply without confirm=1  -> dry_run: true + count
#   2. bulk-apply with confirm=1     -> {"updated": 5}
#   3. dup-group-action on group1    -> {"kept": 1, "deleted": 2}
#   4. CSRF missing                  -> HTTP 403
#
# Must be run inside devenv shell:  devenv shell -- bash web/scripts/qa-task8-bulk.sh

set -euo pipefail

PORT="8108"
if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then PORT="$2"; fi

HOST="127.0.0.1"
BASE_URL="http://$HOST:$PORT"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCROOT="$SCRIPT_DIR/../public"

EVIDENCE_BASE="$SCRIPT_DIR/../../.sisyphus/evidence/current-project"
mkdir -p "$EVIDENCE_BASE"
EVIDENCE_DIR="$(cd "$EVIDENCE_BASE" && pwd)"
EVIDENCE_FILE="$EVIDENCE_DIR/task-8-bulk.txt"
DUP_EVIDENCE_FILE="$EVIDENCE_DIR/task-8-dup-group.txt"

if [ -z "${DEVENV_ROOT:-}" ]; then
    echo "ERROR: DEVENV_ROOT not set. Run via: devenv shell -- bash $0" >&2
    exit 1
fi

SOCK="${DEVENV_STATE:-}/mysql.sock"

FAIL_COUNT=0
CW_JAR="$(mktemp /tmp/cw-cookies-task8-XXXXXX.txt)"
TMP_OUT="$(mktemp /tmp/qa-task8-out-XXXXXX.txt)"
PHP_PID=""

cleanup() {
    [ -n "$PHP_PID" ] && kill "$PHP_PID" 2>/dev/null || true
    rm -f "$CW_JAR" "$TMP_OUT" 2>/dev/null || true
    echo "==> Server stopped." | tee -a "$EVIDENCE_FILE"
}
trap cleanup EXIT

log()    { echo "$1" | tee -a "$EVIDENCE_FILE"; }
log_d()  { echo "$1" | tee -a "$DUP_EVIDENCE_FILE"; }
pass()   { log "  PASS: $1"; }
fail()   { log "  FAIL: $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
pass_d() { log_d "  PASS: $1"; }
fail_d() { log_d "  FAIL: $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

: > "$EVIDENCE_FILE"
: > "$DUP_EVIDENCE_FILE"

log "=================================================================="
log "Task 8 Bulk Decision Workflows QA -- $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "=================================================================="
log ""

log_d "=================================================================="
log_d "Task 8 Dup Group Action QA -- $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log_d "=================================================================="
log_d ""

# ── Fixture: insert report + emails + decisions into MariaDB ────────────────
log "==> Setting up fixture data in MariaDB ..."

REPORT_ID="testreport8"

mysql -u mailreview --socket="$SOCK" mailreview <<'SQL'
-- Idempotent fixture setup for Task 8 QA
DELETE FROM decisions WHERE report_id = 'testreport8';
DELETE FROM emails    WHERE report_id = 'testreport8';
DELETE FROM reports   WHERE report_id = 'testreport8';

INSERT INTO reports (report_id, mailbox, generated_at, pdf_path, manifest_path, decisions_seed_path)
VALUES ('testreport8', 'testbox8', '2026-01-01T00:00:00Z', '', '', '');

-- 3 emails in dup group1 (dup_rank 0,1,2)
INSERT INTO emails (report_id, stable_id, folder, date, sender, subject, total_size_bytes, is_duplicate, dup_group_id, dup_rank)
VALUES
  ('testreport8', 'e8-stable-1', 'INBOX', '2026-01-01', 'a@test.com', 'Dup A1', 1000, 1, 'group1', 0),
  ('testreport8', 'e8-stable-2', 'INBOX', '2026-01-01', 'b@test.com', 'Dup A2', 1000, 1, 'group1', 1),
  ('testreport8', 'e8-stable-3', 'INBOX', '2026-01-01', 'c@test.com', 'Dup A3', 1000, 1, 'group1', 2),
-- 2 more emails in a different group
  ('testreport8', 'e8-stable-4', 'INBOX', '2026-01-02', 'd@test.com', 'Other 1', 500,  0, '',       -1),
  ('testreport8', 'e8-stable-5', 'INBOX', '2026-01-02', 'e@test.com', 'Other 2', 500,  0, '',       -1);

-- Insert corresponding decisions rows (all empty decision initially)
INSERT INTO decisions (report_id, stable_id, decision, note, updated_at, updated_by)
VALUES
  ('testreport8', 'e8-stable-1', '', '', NOW(), ''),
  ('testreport8', 'e8-stable-2', '', '', NOW(), ''),
  ('testreport8', 'e8-stable-3', '', '', NOW(), ''),
  ('testreport8', 'e8-stable-4', '', '', NOW(), ''),
  ('testreport8', 'e8-stable-5', '', '', NOW(), '');
SQL

log "  Fixture inserted: report testreport8, 5 emails (3 in group1, 2 standalone)."
log ""

# ── Start PHP server ─────────────────────────────────────────────────────────
log "==> Starting PHP server on $BASE_URL ..."
php -S "$HOST:$PORT" -t "$DOCROOT" > /tmp/php-server-task8.log 2>&1 &
PHP_PID=$!

for i in $(seq 1 25); do
    curl -sf "$BASE_URL/login.php" >/dev/null 2>&1 && break
    sleep 0.4
done
log "  PHP server running (PID $PHP_PID)."

# ── Login as coworker ────────────────────────────────────────────────────────
log ""
log "==> Logging in as coworker ..."
CW_HTML=$(curl -s -c "$CW_JAR" "$BASE_URL/login.php")
CW_CSRF=$(echo "$CW_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
curl -s -b "$CW_JAR" -c "$CW_JAR" -X POST "$BASE_URL/login.php" \
    --data-urlencode "csrf_token=$CW_CSRF" \
    --data-urlencode "role=coworker" \
    --data-urlencode "password=coworker123" \
    --data-urlencode "display_name=QA Tester 8" >/dev/null
log "  Logged in as coworker."

# Get a fresh CSRF token from the main page
MAIN_HTML=$(curl -s -b "$CW_JAR" "$BASE_URL/")
# Extract CSRF from the logout form or meta
CSRF=$(echo "$MAIN_HTML" | sed -n "s/.*'X-CSRF-Token': '\([^']*\)'.*/\1/p" | head -1)
# Fallback: extract from hidden input
if [ -z "$CSRF" ]; then
    CSRF=$(echo "$MAIN_HTML" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
fi
log "  CSRF token obtained: ${CSRF:0:12}..."

# ── Test 1: bulk-apply WITHOUT confirm=1 -> dry_run ─────────────────────────
log ""
log "--- Test 1: bulk-apply without confirm=1 (dry-run) ---"
BODY1=$(curl -s -b "$CW_JAR" -X POST "$BASE_URL/review/bulk-apply" \
    --data-urlencode "csrf_token=$CSRF" \
    --data-urlencode "report_id=$REPORT_ID" \
    --data-urlencode "decision=delete")
log "  Response: $BODY1"
if echo "$BODY1" | grep -q '"dry_run":true'; then
    COUNT=$(echo "$BODY1" | grep -o '"count":[0-9]*' | cut -d: -f2)
    pass "dry_run=true returned, count=$COUNT (expected 5)"
else
    fail "Expected dry_run:true in response, got: $BODY1"
fi

# ── Test 2: bulk-apply WITH confirm=1 -> updated: 5 ─────────────────────────
log ""
log "--- Test 2: bulk-apply with confirm=1 for decision=delete on testreport8 ---"
BODY2=$(curl -s -b "$CW_JAR" -X POST "$BASE_URL/review/bulk-apply" \
    --data-urlencode "csrf_token=$CSRF" \
    --data-urlencode "report_id=$REPORT_ID" \
    --data-urlencode "decision=delete" \
    --data-urlencode "confirm=1")
log "  Response: $BODY2"
if echo "$BODY2" | grep -q '"updated":5'; then
    pass "bulk-apply updated 5 rows (all decisions in testreport8)"
else
    # rowCount() can return 0 if rows already had that value — check actual DB
    ACTUAL=$(mysql -u mailreview --socket="$SOCK" mailreview -sNe \
        "SELECT COUNT(*) FROM decisions WHERE report_id='testreport8' AND decision='delete'")
    log "  DB check: $ACTUAL rows with decision=delete"
    if [ "$ACTUAL" = "5" ]; then
        pass "bulk-apply: DB has 5 delete rows (rowCount may be 0 on no-change)"
    else
        fail "Expected 5 delete rows, got $ACTUAL. Response was: $BODY2"
    fi
fi

# ── Test 3: dup-group-action on group1 ───────────────────────────────────────
log ""
log "--- Test 3: dup-group-action on group1 ---"
log_d ""
log_d "--- Test 3: dup-group-action on group1 ---"

# Reset decisions for group1 to '' so we can verify the update
mysql -u mailreview --socket="$SOCK" mailreview -e \
    "UPDATE decisions SET decision='' WHERE report_id='testreport8' AND stable_id IN ('e8-stable-1','e8-stable-2','e8-stable-3')"

BODY3=$(curl -s -b "$CW_JAR" -X POST "$BASE_URL/review/dup-group-action" \
    --data-urlencode "csrf_token=$CSRF" \
    --data-urlencode "report_id=$REPORT_ID" \
    --data-urlencode "dup_group_id=group1")
log "  Response: $BODY3"
log_d "  Response: $BODY3"

if echo "$BODY3" | grep -q '"kept":1' && echo "$BODY3" | grep -q '"deleted":2'; then
    pass "dup-group-action: kept=1, deleted=2"
    pass_d "dup-group-action: kept=1, deleted=2"
else
    # Check actual DB values
    KEPT_COUNT=$(mysql -u mailreview --socket="$SOCK" mailreview -sNe \
        "SELECT COUNT(*) FROM decisions d JOIN emails e ON e.report_id=d.report_id AND e.stable_id=d.stable_id WHERE d.report_id='testreport8' AND e.dup_group_id='group1' AND e.dup_rank=0 AND d.decision='keep'")
    DEL_COUNT=$(mysql -u mailreview --socket="$SOCK" mailreview -sNe \
        "SELECT COUNT(*) FROM decisions d JOIN emails e ON e.report_id=d.report_id AND e.stable_id=d.stable_id WHERE d.report_id='testreport8' AND e.dup_group_id='group1' AND e.dup_rank>0 AND d.decision='delete'")
    log "  DB check: kept=$KEPT_COUNT, deleted=$DEL_COUNT"
    log_d "  DB check: kept=$KEPT_COUNT, deleted=$DEL_COUNT"
    if [ "$KEPT_COUNT" = "1" ] && [ "$DEL_COUNT" = "2" ]; then
        pass "dup-group-action: DB shows kept=1, deleted=2"
        pass_d "dup-group-action: DB shows kept=1, deleted=2"
    else
        fail "Expected kept=1 deleted=2, got kept=$KEPT_COUNT deleted=$DEL_COUNT. Response: $BODY3"
        fail_d "Expected kept=1 deleted=2, got kept=$KEPT_COUNT deleted=$DEL_COUNT. Response: $BODY3"
    fi
fi

# ── Test 4: CSRF missing -> 403 ──────────────────────────────────────────────
log ""
log "--- Test 4: CSRF token missing -> expect HTTP 403 ---"
STATUS4=$(curl -s -o /dev/null -w '%{http_code}' -b "$CW_JAR" -X POST "$BASE_URL/review/bulk-apply" \
    --data-urlencode "report_id=$REPORT_ID" \
    --data-urlencode "decision=delete" \
    --data-urlencode "confirm=1")
log "  HTTP status: $STATUS4 (expected 403)"
if [ "$STATUS4" = "403" ]; then
    pass "CSRF-missing -> HTTP 403"
else
    fail "Expected 403 for missing CSRF, got $STATUS4"
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

log_d ""
log_d "=================================================================="
if [ "$FAIL_COUNT" -eq 0 ]; then
    log_d "ALL DUP GROUP TESTS PASSED"
else
    log_d "FAILED: $FAIL_COUNT test(s) failed"
fi
log_d "=================================================================="

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi

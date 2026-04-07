#!/usr/bin/env bash
# web/scripts/qa-archive.sh — End-to-end QA for the mailbox archive pipeline.
#
# Tests ALL pipeline steps against local fixture data.
# No server access required. Run from inside devenv shell.
#
# Usage: bash web/scripts/qa-archive.sh
# Exit codes: 0 = all assertions passed, 1 = any assertion failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVENV_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$DEVENV_ROOT/tests/fixtures"
SOCKET="${MYSQL_UNIX_PORT:-${DEVENV_STATE:-}/mysql.sock}"
MAILBOX="qa_test_mailbox"
GLOBAL_INDEX="$DEVENV_ROOT/data/index/mail_index.sqlite"

cleanup() {
    echo ""
    echo "==> [qa-archive] Cleaning up fixture data..."
    rm -rf "$DEVENV_ROOT/data/mailboxes/$MAILBOX" \
           "$DEVENV_ROOT/data/index/mail_index.sqlite"
    # Remove fixture rows from MySQL (if DB is running)
    if [ -S "$SOCKET" ]; then
        mysql -u mailreview --socket="$SOCKET" mailreview \
            -e "DELETE FROM archive_emails WHERE mailbox='$MAILBOX';" 2>/dev/null || true
        mysql -u mailreview --socket="$SOCKET" mailreview \
            -e "DELETE FROM archive_attachments WHERE mailbox='$MAILBOX';" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "==> [qa-archive] Step 1: ensure DB is running and migrated..."
db-start 2>/dev/null || true
db-migrate --socket "$SOCKET"

echo ""
echo "==> [qa-archive] Step 2: sync fixture mailbox (no server)..."
sync-all \
    --mailboxes-file "$FIXTURES/mailboxes.txt" \
    --src-base "$FIXTURES/src" \
    --skip-import

echo ""
echo "==> [qa-archive] Step 3: verify local SQLite was built..."
if [ ! -f "$DEVENV_ROOT/data/mailboxes/$MAILBOX/index.sqlite" ]; then
    echo "FAIL: per-mailbox index.sqlite not created"; exit 1
fi
if [ ! -f "$GLOBAL_INDEX" ]; then
    echo "FAIL: global mail_index.sqlite not created"; exit 1
fi
SQLITE_COUNT=$(sqlite3 "$DEVENV_ROOT/data/mailboxes/$MAILBOX/index.sqlite" \
    "SELECT COUNT(*) FROM emails")
if [ "$SQLITE_COUNT" -lt 2 ]; then
    echo "FAIL: expected at least 2 emails in SQLite, got $SQLITE_COUNT"; exit 1
fi
echo "  SQLite emails: $SQLITE_COUNT ✓"

echo ""
echo "==> [qa-archive] Step 4: MySQL import..."
php "$DEVENV_ROOT/web/src/cli/import_archive.php" \
    --sqlite "$GLOBAL_INDEX"

echo ""
echo "==> [qa-archive] Step 5: verify MySQL rows..."
EMAIL_COUNT=$(mysql -u mailreview --socket="$SOCKET" mailreview -sNe \
    "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$MAILBOX'")
if [ "$EMAIL_COUNT" -lt 2 ]; then
    echo "FAIL: expected at least 2 rows in archive_emails, got $EMAIL_COUNT"; exit 1
fi
echo "  archive_emails ($MAILBOX): $EMAIL_COUNT rows ✓"

ATT_COUNT=$(mysql -u mailreview --socket="$SOCKET" mailreview -sNe \
    "SELECT COUNT(*) FROM archive_attachments WHERE mailbox='$MAILBOX'")
echo "  archive_attachments ($MAILBOX): $ATT_COUNT rows ✓"

echo ""
echo "==> [qa-archive] Step 6: search-archive test..."
RESULT=$(search-archive "fixture_unique_keyword_alpha" 2>&1 || true)
if ! echo "$RESULT" | grep -qi "$MAILBOX"; then
    echo "FAIL: search-archive did not return '$MAILBOX' for known keyword"
    echo "Output was:"
    echo "$RESULT"
    exit 1
fi
echo "  search-archive: result contains '$MAILBOX' ✓"

echo ""
echo "==> [qa-archive] Step 7: verify cc_addrs indexed..."
CC_COUNT=$(mysql -u mailreview --socket="$SOCKET" mailreview -sNe \
    "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$MAILBOX' AND cc_addrs != ''")
if [ "$CC_COUNT" -lt 1 ]; then
    echo "FAIL: no emails with cc_addrs found — CC extraction may be broken"; exit 1
fi
echo "  cc_addrs populated: $CC_COUNT email(s) ✓"

echo ""
echo "==> [qa-archive] Step 8: verify body_text indexed..."
BODY_COUNT=$(mysql -u mailreview --socket="$SOCKET" mailreview -sNe \
    "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$MAILBOX' AND LENGTH(body_text) > 0")
if [ "$BODY_COUNT" -lt 2 ]; then
    echo "FAIL: expected body_text in at least 2 emails, got $BODY_COUNT"; exit 1
fi
echo "  body_text populated: $BODY_COUNT email(s) ✓"

echo ""
echo "==> [qa-archive] ALL STEPS PASSED ✓"

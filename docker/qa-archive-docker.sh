#!/usr/bin/env bash
# docker/qa-archive-docker.sh — QA script adapted for Docker (no devenv required).
# Mirrors web/scripts/qa-archive.sh but calls Python/PHP directly.
#
# Usage: bash docker/qa-archive-docker.sh
# Exit codes: 0 = all assertions passed, 1 = any assertion failed

set -euo pipefail

DEVENV_ROOT=/app
FIXTURES="$DEVENV_ROOT/tests/fixtures"
MAILBOX="qa_test_mailbox"
GLOBAL_INDEX="$DEVENV_ROOT/data/index/mail_index.sqlite"

_mysql() {
    mysql -h "${DB_HOST:-db}" -P "${DB_PORT:-3306}" \
          -u "${DB_USER:-mailreview}" -p"${DB_PASS:-mailreview}" \
          "${DB_NAME:-mailreview}" "$@"
}

cleanup() {
    echo ""
    echo "==> [qa-archive] Cleaning up fixture data..."
    rm -rf "$DEVENV_ROOT/data/mailboxes/$MAILBOX" \
           "$DEVENV_ROOT/data/index/mail_index.sqlite" || true
    _mysql -e "DELETE FROM archive_emails WHERE mailbox='$MAILBOX';" 2>/dev/null || true
    _mysql -e "DELETE FROM archive_attachments WHERE mailbox='$MAILBOX';" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> [qa-archive] Step 1: run migrations..."
php "$DEVENV_ROOT/web/src/cli/migrate.php" 2>&1

echo ""
echo "==> [qa-archive] Step 2: sync fixture mailbox..."
MB="$MAILBOX"
DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MB"
MAILDIR_DST="$DATA_ROOT/maildir/.maildir"
ATT_DST="$DATA_ROOT/attachments"
mkdir -p "$MAILDIR_DST" "$ATT_DST" "$DEVENV_ROOT/data/index"

echo "  rsync fixtures -> $MAILDIR_DST/"
rsync -az "$FIXTURES/src/$MB/.maildir/" "$MAILDIR_DST/"

echo "  extracting attachments..."
PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
    --maildir-root "$MAILDIR_DST" \
    --output-root  "$ATT_DST"

echo "  indexing..."
PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
    --mailbox "$MB" \
    --data-root "$DATA_ROOT" \
    --global-index "$GLOBAL_INDEX"

echo ""
echo "==> [qa-archive] Step 3: verify local SQLite was built..."
if [ ! -f "$DATA_ROOT/index.sqlite" ]; then
    echo "FAIL: per-mailbox index.sqlite not created"; exit 1
fi
if [ ! -f "$GLOBAL_INDEX" ]; then
    echo "FAIL: global mail_index.sqlite not created"; exit 1
fi
SQLITE_COUNT=$(sqlite3 "$DATA_ROOT/index.sqlite" "SELECT COUNT(*) FROM emails")
if [ "$SQLITE_COUNT" -lt 2 ]; then
    echo "FAIL: expected at least 2 emails in SQLite, got $SQLITE_COUNT"; exit 1
fi
echo "  SQLite emails: $SQLITE_COUNT ✓"

echo ""
echo "==> [qa-archive] Step 4: MySQL import..."
php "$DEVENV_ROOT/web/src/cli/import_archive.php" --sqlite "$GLOBAL_INDEX"

echo ""
echo "==> [qa-archive] Step 5: verify MySQL rows..."
EMAIL_COUNT=$(_mysql -sNe "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$MAILBOX'" 2>/dev/null)
if [ "$EMAIL_COUNT" -lt 2 ]; then
    echo "FAIL: expected at least 2 rows in archive_emails, got $EMAIL_COUNT"; exit 1
fi
echo "  archive_emails ($MAILBOX): $EMAIL_COUNT rows ✓"

ATT_COUNT=$(_mysql -sNe "SELECT COUNT(*) FROM archive_attachments WHERE mailbox='$MAILBOX'" 2>/dev/null)
echo "  archive_attachments ($MAILBOX): $ATT_COUNT rows ✓"

echo ""
echo "==> [qa-archive] Step 6: search-archive test..."
RESULT=$(php "$DEVENV_ROOT/web/src/cli/search_archive.php" --query "fixture_unique_keyword_alpha" 2>&1 || true)
if ! echo "$RESULT" | grep -qi "$MAILBOX"; then
    echo "FAIL: search did not return '$MAILBOX' for known keyword"
    echo "Output was:"
    echo "$RESULT"
    exit 1
fi
echo "  search: result contains '$MAILBOX' ✓"

echo ""
echo "==> [qa-archive] Step 7: verify cc_addrs indexed..."
CC_COUNT=$(_mysql -sNe "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$MAILBOX' AND cc_addrs != ''" 2>/dev/null)
if [ "$CC_COUNT" -lt 1 ]; then
    echo "FAIL: no emails with cc_addrs found — CC extraction may be broken"; exit 1
fi
echo "  cc_addrs populated: $CC_COUNT email(s) ✓"

echo ""
echo "==> [qa-archive] Step 8: verify body_text indexed..."
BODY_COUNT=$(_mysql -sNe "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$MAILBOX' AND LENGTH(body_text) > 0" 2>/dev/null)
if [ "$BODY_COUNT" -lt 2 ]; then
    echo "FAIL: expected body_text in at least 2 emails, got $BODY_COUNT"; exit 1
fi
echo "  body_text populated: $BODY_COUNT email(s) ✓"

echo ""
echo "==> [qa-archive] ALL STEPS PASSED ✓"

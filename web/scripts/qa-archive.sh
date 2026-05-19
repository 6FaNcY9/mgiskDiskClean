#!/usr/bin/env bash
# web/scripts/qa-archive.sh — End-to-end QA for the mailbox archive pipeline.
# Dev-only helper for the optional devenv workflow.
#
# Creates a synthetic mailbox, runs: rsync-skip → extract → index → import
# and verifies each step's output.
#
# Usage: bash web/scripts/qa-archive.sh [--quiet]
# Prerequisites: db-start, db-migrate must have run.

set -euo pipefail

DEVENV_ROOT="${DEVENV_ROOT:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
QUIET=0
if [ "${1:-}" = "--quiet" ]; then QUIET=1; fi

log() { [ "$QUIET" -eq 0 ] && echo "$@" || true; }
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { log "  PASS: $*"; }

# ── Setup synthetic mailbox ───────────────────────────────────────────────────
QA_MAILBOX="qa-archive-test-$$"
DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$QA_MAILBOX"
MAILDIR="$DATA_ROOT/maildir/.maildir/cur"
ATTACHMENTS="$DATA_ROOT/attachments"
GLOBAL_INDEX="$DEVENV_ROOT/data/index/mail_index.sqlite"

log "==> [qa-archive] Creating synthetic mailbox: $QA_MAILBOX"
mkdir -p "$MAILDIR" "$DATA_ROOT/maildir/.maildir/new" "$DATA_ROOT/maildir/.maildir/tmp"
mkdir -p "$ATTACHMENTS"

# Write a synthetic email
export QA_MAILDIR="$MAILDIR"
python3 - <<'PYEOF'
import sys, os, pathlib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

maildir = os.environ.get("QA_MAILDIR")
msg = MIMEMultipart("mixed")
msg["Subject"] = "QA Archive Test Email"
msg["From"] = "qa-sender@example.com"
msg["To"] = "qa-receiver@example.com"
msg["Cc"] = "qa-cc@example.com"
msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
msg["Message-ID"] = "<qa-archive-test@example.com>"
msg.attach(MIMEText("QA body text for full-text search.", "plain", "utf-8"))
att = MIMEApplication(b"QA attachment data", Name="qa-test.txt")
att["Content-Disposition"] = 'attachment; filename="qa-test.txt"'
msg.attach(att)
out = pathlib.Path(maildir) / "qa-test.msg"
out.write_bytes(msg.as_bytes())
print(f"  wrote: {out}")
PYEOF

log "==> Step 1: Extract attachments"
PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
  --maildir-root "$DATA_ROOT/maildir/.maildir" --output-root "$ATTACHMENTS" \
  || fail "extract_attachments failed"
ATT_COUNT=$(ls "$ATTACHMENTS" | wc -l | tr -d ' ')
[ "$ATT_COUNT" -ge 1 ] && pass "attachments extracted: $ATT_COUNT files" || fail "No attachments extracted"

log "==> Step 2: Index mailbox"
mkdir -p "$DEVENV_ROOT/data/index"
PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
  --mailbox "$QA_MAILBOX" \
  --data-root "$DATA_ROOT" \
  --global-index "$GLOBAL_INDEX" \
  || fail "index_mailbox failed"

[ -f "$DATA_ROOT/index.sqlite" ] && pass "per-mailbox index created" || fail "index.sqlite missing"

EMAIL_COUNT=$(sqlite3 "$DATA_ROOT/index.sqlite" "SELECT COUNT(*) FROM emails;")
[ "$EMAIL_COUNT" -ge 1 ] && pass "emails indexed: $EMAIL_COUNT" || fail "No emails in index"

CC_VAL=$(sqlite3 "$DATA_ROOT/index.sqlite" "SELECT cc_addrs FROM emails LIMIT 1;")
echo "$CC_VAL" | grep -q "qa-cc@example.com" && pass "cc_addrs stored correctly" || fail "cc_addrs not stored: got '$CC_VAL'"

BODY_VAL=$(sqlite3 "$DATA_ROOT/index.sqlite" "SELECT body_text FROM emails LIMIT 1;")
echo "$BODY_VAL" | grep -q "QA body text" && pass "body_text stored correctly" || fail "body_text not stored: got '$BODY_VAL'"

log "==> Step 3: Import into MySQL"
SOCK="${MYSQL_UNIX_PORT:-${DEVENV_STATE:-$DEVENV_ROOT/.devenv/state}/mysql.sock}"
php "$DEVENV_ROOT/web/src/cli/import_archive.php" \
  --sqlite "$DATA_ROOT/index.sqlite" \
  --socket "$SOCK" \
  ${QUIET:+--quiet} \
  || fail "import_archive.php failed"

MYSQL_COUNT=$(mariadb -u mailreview --socket="$SOCK" mailreview \
  -e "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$QA_MAILBOX';" \
  --skip-column-names 2>/dev/null)
[ "${MYSQL_COUNT:-0}" -ge 1 ] && pass "emails in MySQL: $MYSQL_COUNT" || fail "No emails in archive_emails"

ATT_MYSQL=$(mariadb -u mailreview --socket="$SOCK" mailreview \
  -e "SELECT COUNT(*) FROM archive_attachments WHERE mailbox='$QA_MAILBOX';" \
  --skip-column-names 2>/dev/null)
[ "${ATT_MYSQL:-0}" -ge 1 ] && pass "attachments in MySQL: $ATT_MYSQL" || fail "No attachments in archive_attachments"

log "==> Step 4: Verify FULLTEXT search"
FT_RESULT=$(mariadb -u mailreview --socket="$SOCK" mailreview \
  -e "SELECT stable_id FROM archive_emails WHERE MATCH(subject,from_addr,to_addrs,cc_addrs,body_text) AGAINST('QA body text' IN BOOLEAN MODE) AND mailbox='$QA_MAILBOX';" \
  --skip-column-names 2>/dev/null)
[ -n "$FT_RESULT" ] && pass "FULLTEXT search returned results" || fail "FULLTEXT search returned no results"

log "==> Cleanup"
mariadb -u mailreview --socket="$SOCK" mailreview \
  -e "DELETE FROM archive_emails WHERE mailbox='$QA_MAILBOX'; DELETE FROM archive_attachments WHERE mailbox='$QA_MAILBOX';" \
  2>/dev/null && pass "MySQL cleanup done" || true
rm -rf "$DATA_ROOT" && pass "synthetic mailbox removed" || true

echo ""
echo "==> [qa-archive] ALL CHECKS PASSED"

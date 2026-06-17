# Mailbox Archive Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the decision/deletion review workflow with a weekly rsync archive pipeline: download all mailboxes → extract attachments → SQLite index → MySQL archive → terminal search.

**Architecture:** Python pipeline (`sync-all` devenv command) rsyncs each mailbox listed in `data/mailboxes.txt`, extracts attachments, and rebuilds the SQLite index. A PHP CLI script (`import_archive.php`) upserts the indexed data into MariaDB archive tables. A `search-archive` devenv command queries MySQL FULLTEXT indexes from the terminal. No web UI in this phase.

**Tech Stack:** Python 3.11+, PHP 8.3 (CLI only), MariaDB/MySQL FULLTEXT, SQLite WAL, devenv/Nix, pytest.

---

## Task 1: Delete obsolete files

Files removed in this task are specified by the design spec and will break tests that reference the old workflow.

**Files:**
- Delete: `src/maildir_report/apply_decisions.py`
- Delete: `src/maildir_report/decisions_template.py`
- Delete: `src/maildir_report/pdf.py`
- Delete: `src/maildir_report/manifest.py`
- Delete: `src/maildir_report/imap_ingest.py`
- Delete: `src/maildir_report/pre_store_dedup.py`
- Delete: `src/maildir_report/cli.py`
- Delete: `src/maildir_report/__main__.py`
- Delete: `tests/test_apply_decisions.py`
- Delete: `tests/test_decisions_template.py`
- Delete: `tests/test_pdf_german_headers.py`
- Delete: `tests/test_imap_ingest.py`
- Delete: `tests/test_pre_store_dedup.py`
- Delete: `tests/test_e2e_cli.py`
- Delete: `web/src/Services/ReviewService.php`
- Delete: `web/src/Services/I18nService.php`
- Delete: `web/src/Import/Importer.php`
- Delete: `web/src/Import/ImportException.php`
- Delete: `web/public/login.php`
- Delete: `web/public/index.php`
- Delete: `web/migrations/001_initial_schema.sql`
- Delete: `web/scripts/qa-task5-auth.sh`
- Delete: `web/scripts/qa-task5-csrf.sh`
- Delete: `web/scripts/qa-task6-csrf-ui-method.sh`
- Delete: `web/scripts/qa-task6-ui.sh`
- Delete: `web/scripts/qa-task7-attachments.sh`
- Delete: `web/scripts/qa-task8-bulk.sh`
- Delete: `web/scripts/qa-task9-export.sh`
- Delete: `web/scripts/qa-task10-hardening.sh`
- Delete: `web/scripts/qa-health.sh`
- Delete: `web/scripts/qa-config-missing.sh`

**Step 1: Delete Python source files**

```bash
cd src/maildir_report
rm apply_decisions.py decisions_template.py pdf.py manifest.py \
   imap_ingest.py pre_store_dedup.py cli.py __main__.py
```

**Step 2: Delete obsolete test files**

```bash
cd tests
rm test_apply_decisions.py test_decisions_template.py \
   test_pdf_german_headers.py test_imap_ingest.py \
   test_pre_store_dedup.py test_e2e_cli.py
```

**Step 3: Delete PHP files**

```bash
rm web/src/Services/ReviewService.php web/src/Services/I18nService.php
rm web/src/Import/Importer.php web/src/Import/ImportException.php
rm web/public/login.php web/public/index.php
rm web/migrations/001_initial_schema.sql
rm web/scripts/qa-task5-auth.sh web/scripts/qa-task5-csrf.sh \
   web/scripts/qa-task6-csrf-ui-method.sh web/scripts/qa-task6-ui.sh \
   web/scripts/qa-task7-attachments.sh web/scripts/qa-task8-bulk.sh \
   web/scripts/qa-task9-export.sh web/scripts/qa-task10-hardening.sh \
   web/scripts/qa-health.sh web/scripts/qa-config-missing.sh
```

**Step 4: Remove the now-empty Import/ and Services/ directories**

```bash
rmdir web/src/Import web/src/Services
```

**Step 5: Verify remaining tests pass**

```bash
pytest -q
```

Expected: all remaining tests pass. (Some tests like `test_pdf_determinism.py`, `test_manifest.py` that import deleted modules will fail — that is expected; they must also be deleted.)

Wait — check: `test_manifest.py` imports `manifest.py` which is deleted. It is NOT in the delete list above. Add it:

- Also delete: `tests/test_manifest.py`
- Also delete: `tests/test_pdf_determinism.py`
- Also delete: `tests/test_pdf_duplicates.py`

```bash
rm tests/test_manifest.py tests/test_pdf_determinism.py tests/test_pdf_duplicates.py
```

**Step 6: Run pytest again**

```bash
pytest -q
```

Expected: passes. If any test imports a now-deleted module, delete that test too. Acceptable remaining tests: `test_dedup_group.py`, `test_inventory_reconcile.py`, `test_rfc822.py`, `test_scaffold.py`, `test_sha256.py`, `test_stable_ids.py`, `test_strict_parse.py`, `test_task2b_attachments_index.py`, `test_walk_deterministic.py`.

**Step 7: Commit**

```bash
git add -A
git commit -m "chore: remove obsolete decision/PDF/IMAP workflow files per archive spec"
```

---

## Task 2: Create archive MySQL schema migration

**Files:**
- Create: `web/migrations/001_archive_schema.sql`

**Step 1: Create the migration file**

Create `web/migrations/001_archive_schema.sql`:

```sql
-- 001_archive_schema.sql
-- Archive schema: replaces the old review/decision tables.
-- Applied by: php web/src/cli/migrate.php

CREATE TABLE IF NOT EXISTS archive_emails (
    mailbox          VARCHAR(255) NOT NULL,
    stable_id        CHAR(64)     NOT NULL,
    filepath         TEXT         NOT NULL,
    folder           VARCHAR(255) NOT NULL DEFAULT '',
    date             VARCHAR(64)  NOT NULL DEFAULT '',
    from_addr        VARCHAR(255) NOT NULL DEFAULT '',
    to_addrs         TEXT         NOT NULL DEFAULT '',
    cc_addrs         TEXT         NOT NULL DEFAULT '',
    subject          TEXT         NOT NULL DEFAULT '',
    body_text        LONGTEXT     NOT NULL DEFAULT '',
    total_size_bytes BIGINT       NOT NULL DEFAULT 0,
    imported_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mailbox, stable_id),
    KEY idx_date     (mailbox, date),
    FULLTEXT KEY ftx_email (subject, from_addr, to_addrs, cc_addrs, body_text)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS archive_attachments (
    mailbox           VARCHAR(255) NOT NULL,
    email_stable_id   CHAR(64)     NOT NULL,
    stored_path       TEXT         NOT NULL,
    sha256            CHAR(64)     NOT NULL,
    size              BIGINT       NOT NULL DEFAULT 0,
    mime              VARCHAR(255) NOT NULL DEFAULT '',
    original_filename TEXT         NOT NULL DEFAULT '',
    imported_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mailbox, email_stable_id, sha256),
    KEY idx_email  (mailbox, email_stable_id),
    KEY idx_sha256 (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Step 2: Verify syntax by running the migration**

```bash
db-start   # ensure MariaDB is running
db-migrate
```

Expected output:
```
  [apply] 001_archive_schema.sql
==> Migrations complete. Applied: 1
```

**Step 3: Check tables were created**

```bash
mysql -u mailreview --socket="$DEVENV_STATE/mysql.sock" mailreview \
  -e "SHOW TABLES; SHOW CREATE TABLE archive_emails\G"
```

Expected: both `archive_emails` and `archive_attachments` present with FULLTEXT index.

**Step 4: Commit**

```bash
git add web/migrations/001_archive_schema.sql
git commit -m "feat(db): add archive_emails and archive_attachments MySQL schema"
```

---

## Task 3: Create mailbox list file

**Files:**
- Create: `data/mailboxes.txt`

The file lists one mailbox name per line. Lines starting with `#` are comments. Empty lines are ignored. Mailbox names must match `[a-zA-Z0-9._-]+` (the regex used by `sync-all` for validation).

**Step 1: Create `data/mailboxes.txt`**

```
# data/mailboxes.txt
# One mailbox name per line. Comments start with #. Empty lines ignored.
# Mailbox names: letters, digits, dots, hyphens, underscores only.
# Example:
# gabriel.hangel
# info
```

Create it as an empty placeholder (boss will fill in actual names):

```
# data/mailboxes.txt
# One mailbox name per line. Comments (#) and blank lines are ignored.
# Allowed characters: [a-zA-Z0-9._-]
# Example:
#   gabriel.hangel
#   info
#   office
```

**Step 2: Verify the file is tracked by git**

```bash
git add data/mailboxes.txt
git status
```

Expected: `data/mailboxes.txt` staged as new file.

**Step 3: Commit**

```bash
git commit -m "chore: add data/mailboxes.txt placeholder for sync-all mailbox list"
```

---

## Task 4: Add `extract-attachments` devenv command

**Files:**
- Modify: `devenv.nix` (add `extract-attachments` script block)

The command wraps `python3 -m maildir_report.extract_attachments`. The module already exists at `src/maildir_report/extract_attachments.py` and has a `main()` CLI entrypoint.

**Step 1: Check extract_attachments.py has a main() CLI**

```bash
grep -n "def main" src/maildir_report/extract_attachments.py
python3 -c "import sys; sys.path.insert(0,'src'); from maildir_report import extract_attachments; print(hasattr(extract_attachments, 'main'))"
```

Expected: `True` (main exists). If not, check `__main__` block.

**Step 2: Add extract-attachments script to devenv.nix**

In `devenv.nix`, after the `index-all` command block (after line 273, before `review-start`), add:

```nix
    # ── extract-attachments: extract MIME attachments from stored maildir ──
    extract-attachments.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: extract-attachments <mailbox>"
        echo ""
        echo "  Extract MIME attachments from the stored Maildir for <mailbox>."
        echo "  Reads from:  \$DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir/"
        echo "  Writes to:   \$DEVENV_ROOT/data/mailboxes/<mailbox>/attachments/"
        echo ""
        echo "  Idempotent: re-running skips already-extracted files."
        echo ""
        echo "Options:"
        echo "  --help    Show this help message and exit"
        exit 0
      fi
      if [ -z "$1" ]; then
        echo "ERROR: mailbox name required"
        echo "Run: extract-attachments --help"
        exit 1
      fi
      MAILBOX="$1"
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      MAILDIR="$DATA_ROOT/maildir/.maildir"
      ATTACHMENTS="$DATA_ROOT/attachments"
      if [ ! -d "$MAILDIR" ]; then
        echo "ERROR: Maildir not found: $MAILDIR"
        echo "Run sync-all first."
        exit 1
      fi
      mkdir -p "$ATTACHMENTS"
      echo "==> [extract-attachments] Extracting attachments for '$MAILBOX'..."
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
        "$MAILDIR" "$ATTACHMENTS" \
        || { echo "ERROR: extract-attachments failed"; exit 1; }
      echo "==> Done. Attachments: $ATTACHMENTS"
    '';
```

**Step 3: Also update the enterShell welcome message**

In the `enterShell` block (lines 409–427), replace the old command list with the new archive commands. Find:
```
  echo "  scan-mailbox <mailbox>         rsync maildir, generate PDF/manifest/decisions"
  echo "  store-mailbox <mailbox>        rsync + dedup + pipeline into data/mailboxes/"
  echo "  fetch-imap <mailbox> <dir>     fetch IMAP mailbox to local Maildir (read-only)"
  echo "  index-mailbox <mailbox>        (re)build per-mailbox SQLite index"
  echo "  index-all                      (re)build global index across all mailboxes"
  echo "  db-start                       start local MariaDB dev server"
  echo "  db-migrate                     run SQL migrations"
  echo "  review-start                   start PHP dev server at http://127.0.0.1:8000"
  echo "  apply-decisions <mb> <csv>     apply reviewed decisions locally"
```

Replace with (update in Task 6 when sync-all and search-archive are added — skip for now).

**Step 4: Reload devenv and test**

```bash
devenv shell -- extract-attachments --help
```

Expected: prints usage without error.

**Step 5: Also remove the deleted commands from devenv.nix**

In `devenv.nix`, remove the following script blocks entirely:
- `scan-mailbox` (lines 55–78)
- `store-mailbox` (lines 128–209)
- `review-start` (lines 276–312)
- `apply-decisions` (lines 314–347)
- `fetch-imap` (lines 349–402)

After removal, also remove their entries from the `enterShell` welcome message.

**Step 6: Verify devenv.nix is valid Nix**

```bash
nix-instantiate --parse devenv.nix 2>&1 | head -5
```

Expected: no parse errors.

**Step 7: Commit**

```bash
git add devenv.nix
git commit -m "feat(devenv): add extract-attachments command; remove obsolete scan/store/review/apply/fetch commands"
```

---

## Task 5: Write red tests for CC + body extraction

**Files:**
- Create: `tests/test_body_cc_extraction.py`

These tests must FAIL before Task 7 (parser implementation). They test `parse_email_file()` returning `cc_addrs` and `body_text` keys — which do not exist yet.

**Step 1: Write the test file**

Create `tests/test_body_cc_extraction.py`:

```python
"""
test_body_cc_extraction.py — Red tests for CC + body extraction in parser.py.

These tests FAIL until Task 7 implements cc_addrs and body_text in parse_email_file().
"""
import pathlib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from maildir_report.parser import parse_email_file


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_mail(tmp_path: pathlib.Path, raw: bytes, name: str = "1.msg") -> str:
    p = tmp_path / name
    p.write_bytes(raw)
    return str(p)


def _plain_mail(
    *,
    subject: str = "Hello",
    from_: str = "alice@example.com",
    to: str = "bob@example.com",
    cc: str = "",
    body: str = "Hello world.",
    message_id: str = "<test@example.com>",
) -> bytes:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["Message-ID"] = message_id
    return msg.as_bytes()


def _multipart_mail(
    *,
    body_plain: str = "Plain body.",
    body_html: str = "<p>HTML body.</p>",
    cc: str = "",
    message_id: str = "<multi@example.com>",
) -> bytes:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Multipart"
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    if cc:
        msg["Cc"] = cc
    msg["Date"] = "Tue, 02 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = message_id
    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg.as_bytes()


def _mail_with_attachment(
    *,
    body: str = "See attachment.",
    cc: str = "",
    attachment_data: bytes = b"PDF content",
    filename: str = "report.pdf",
    message_id: str = "<attach@example.com>",
) -> bytes:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "With attachment"
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    if cc:
        msg["Cc"] = cc
    msg["Date"] = "Wed, 03 Jan 2024 08:00:00 +0000"
    msg["Message-ID"] = message_id
    msg.attach(MIMEText(body, "plain", "utf-8"))
    att = MIMEApplication(attachment_data, Name=filename)
    att["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(att)
    return msg.as_bytes()


# ── cc_addrs tests ────────────────────────────────────────────────────────────

def test_cc_addrs_present_when_cc_header_set(tmp_path):
    """parse_email_file() must return 'cc_addrs' key."""
    raw = _plain_mail(cc="carol@example.com", message_id="<cc1@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "cc_addrs" in rec, "EmailRecord must have 'cc_addrs' key"


def test_cc_addrs_value_matches_header(tmp_path):
    """cc_addrs must match the decoded Cc header value."""
    raw = _plain_mail(cc="carol@example.com, dave@example.com", message_id="<cc2@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "carol@example.com" in rec["cc_addrs"]
    assert "dave@example.com" in rec["cc_addrs"]


def test_cc_addrs_empty_string_when_no_cc(tmp_path):
    """cc_addrs must be an empty string when no Cc header is present."""
    raw = _plain_mail(cc="", message_id="<cc3@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert rec["cc_addrs"] == ""


def test_cc_addrs_decoded_rfc2047(tmp_path):
    """cc_addrs must be a decoded unicode string (not raw RFC 2047 encoded)."""
    # ASCII Cc is enough to verify the field is present and decoded
    raw = _plain_mail(cc="Eve <eve@example.com>", message_id="<cc4@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert isinstance(rec["cc_addrs"], str)
    assert "eve@example.com" in rec["cc_addrs"]


# ── body_text tests ───────────────────────────────────────────────────────────

def test_body_text_present_in_record(tmp_path):
    """parse_email_file() must return 'body_text' key."""
    raw = _plain_mail(body="Test body content.", message_id="<b1@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "body_text" in rec, "EmailRecord must have 'body_text' key"


def test_body_text_plain_extracted(tmp_path):
    """body_text must contain the text/plain body content."""
    raw = _plain_mail(body="Hello archive world.", message_id="<b2@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "Hello archive world." in rec["body_text"]


def test_body_text_from_multipart_alternative(tmp_path):
    """body_text must be extracted from text/plain part of multipart/alternative."""
    raw = _multipart_mail(body_plain="Plain part here.", message_id="<b3@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "Plain part here." in rec["body_text"]


def test_body_text_does_not_contain_html(tmp_path):
    """body_text must NOT contain HTML tags from text/html parts."""
    raw = _multipart_mail(
        body_plain="Plain only.",
        body_html="<p>HTML content here</p>",
        message_id="<b4@x>",
    )
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    # Must contain plain text
    assert "Plain only." in rec["body_text"]
    # Must NOT contain raw HTML tags (text/html is not included)
    assert "<p>" not in rec["body_text"]


def test_body_text_with_attachment_present(tmp_path):
    """body_text works correctly when the email also has attachments."""
    raw = _mail_with_attachment(body="Cover letter text.", message_id="<b5@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "Cover letter text." in rec["body_text"]


def test_body_text_empty_string_for_attachment_only(tmp_path):
    """body_text is empty string when no text/plain part exists."""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "No body"
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["Message-ID"] = "<b6@x>"
    att = MIMEApplication(b"data", Name="data.bin")
    att["Content-Disposition"] = 'attachment; filename="data.bin"'
    msg.attach(att)
    path = _write_mail(tmp_path, msg.as_bytes())
    rec = parse_email_file(path, "INBOX")
    assert rec["body_text"] == ""


def test_body_text_is_string_type(tmp_path):
    """body_text must always be a str, never bytes."""
    raw = _plain_mail(body="String check.", message_id="<b7@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert isinstance(rec["body_text"], str)


def test_body_text_charset_fallback_latin1(tmp_path):
    """body_text handles latin-1 encoded bodies without crashing."""
    # Build a raw email with latin-1 body
    raw_body = "Caf\xe9 und Str\xae\xdf e"  # latin-1 bytes in string
    msg = MIMEText.__new__(MIMEText)
    import email.mime.text as _t
    # Build manually to get latin-1 encoding
    from email.mime.text import MIMEText as MT
    part = MT(raw_body, "plain", "latin-1")
    part["Subject"] = "Encoding test"
    part["From"] = "x@example.com"
    part["To"] = "y@example.com"
    part["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    part["Message-ID"] = "<b8@x>"
    path = _write_mail(tmp_path, part.as_bytes())
    rec = parse_email_file(path, "INBOX")
    # Must not raise; must be a str
    assert isinstance(rec["body_text"], str)
    assert len(rec["body_text"]) > 0
```

**Step 2: Run to verify tests FAIL**

```bash
pytest tests/test_body_cc_extraction.py -v
```

Expected: ALL tests fail with `KeyError: 'cc_addrs'` or `KeyError: 'body_text'` (the keys don't exist yet in the parser output). If any test passes, the parser already returns that field — investigate and adjust.

**Step 3: Commit the failing tests**

```bash
git add tests/test_body_cc_extraction.py
git commit -m "test(parser): red tests for cc_addrs and body_text extraction"
```

---

## Task 6: Add `sync-all` devenv command

**Files:**
- Modify: `devenv.nix` (add `sync-all` script block)
- Update: `devenv.nix` `enterShell` welcome message

Depends on: Task 3 (`data/mailboxes.txt` format), Task 4 (`extract-attachments` command exists).

**Step 1: Add sync-all script to devenv.nix**

Add after the `extract-attachments` block (before the `enterShell` section):

```nix
    # ── sync-all: weekly full archive sync for all mailboxes ─────────────
    sync-all.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: sync-all [--mailbox <name>] [--skip-rsync] [--skip-extract] [--skip-index]"
        echo ""
        echo "  Sync all mailboxes listed in data/mailboxes.txt:"
        echo "    1. rsync each mailbox from the remote server"
        echo "    2. extract-attachments for each mailbox"
        echo "    3. index-mailbox (per-mailbox + global SQLite index)"
        echo ""
        echo "  Remote source: mrija_org@s16.thehost.com.ua:email/mrija.org/<mailbox>/.maildir/"
        echo ""
        echo "Options:"
        echo "  --mailbox <name>   Sync only this mailbox (overrides mailboxes.txt)"
        echo "  --skip-rsync       Skip rsync step (use existing local data)"
        echo "  --skip-extract     Skip attachment extraction step"
        echo "  --skip-index       Skip SQLite indexing step"
        echo "  --help             Show this help message and exit"
        exit 0
      fi

      MAILBOXES_FILE="$DEVENV_ROOT/data/mailboxes.txt"
      GLOBAL_INDEX_DIR="$DEVENV_ROOT/data/index"
      REMOTE_BASE="mrija_org@s16.thehost.com.ua:email/mrija.org"

      SINGLE_MAILBOX=""
      SKIP_RSYNC=0
      SKIP_EXTRACT=0
      SKIP_INDEX=0
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox) SINGLE_MAILBOX="$2"; shift 2 ;;
          --skip-rsync) SKIP_RSYNC=1; shift ;;
          --skip-extract) SKIP_EXTRACT=1; shift ;;
          --skip-index) SKIP_INDEX=1; shift ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      if [ -n "$SINGLE_MAILBOX" ]; then
        MAILBOX_LIST="$SINGLE_MAILBOX"
      else
        if [ ! -f "$MAILBOXES_FILE" ]; then
          echo "ERROR: $MAILBOXES_FILE not found."
          echo "  Create it with one mailbox name per line."
          exit 1
        fi
        # Read mailboxes: strip comments (#) and blank lines, validate names
        MAILBOX_LIST=""
        while IFS= read -r line || [ -n "$line" ]; do
          # Strip leading/trailing whitespace
          line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
          # Skip comments and empty lines
          case "$line" in
            "#"*|"") continue ;;
          esac
          # Validate: only [a-zA-Z0-9._-]
          if ! echo "$line" | grep -qE '^[a-zA-Z0-9._-]+$'; then
            echo "ERROR: Invalid mailbox name in $MAILBOXES_FILE: '$line'"
            echo "  Allowed characters: letters, digits, dots, hyphens, underscores"
            exit 1
          fi
          MAILBOX_LIST="$MAILBOX_LIST $line"
        done < "$MAILBOXES_FILE"
        MAILBOX_LIST="$(echo "$MAILBOX_LIST" | sed 's/^[[:space:]]*//')"
        if [ -z "$MAILBOX_LIST" ]; then
          echo "ERROR: No mailboxes found in $MAILBOXES_FILE"
          exit 1
        fi
      fi

      mkdir -p "$GLOBAL_INDEX_DIR"

      for MAILBOX in $MAILBOX_LIST; do
        echo ""
        echo "==> [sync-all] Processing mailbox: $MAILBOX"
        DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
        MAILDIR_DST="$DATA_ROOT/maildir/.maildir"

        # Step 1: rsync
        if [ "$SKIP_RSYNC" -eq 0 ]; then
          mkdir -p "$MAILDIR_DST"
          echo "    rsync from $REMOTE_BASE/$MAILBOX/.maildir/..."
          rsync -az --info=progress2 \
            "$REMOTE_BASE/$MAILBOX/.maildir/" \
            "$MAILDIR_DST/" \
            || { echo "ERROR: rsync failed for $MAILBOX"; exit 1; }
          echo "    rsync done."
        else
          echo "    [skip-rsync] skipping rsync for $MAILBOX"
        fi

        # Step 2: extract attachments
        if [ "$SKIP_EXTRACT" -eq 0 ]; then
          echo "    extracting attachments..."
          PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
            "$MAILDIR_DST" "$DATA_ROOT/attachments" \
            || { echo "ERROR: extract-attachments failed for $MAILBOX"; exit 1; }
          echo "    extraction done."
        else
          echo "    [skip-extract] skipping extraction for $MAILBOX"
        fi

        # Step 3: index
        if [ "$SKIP_INDEX" -eq 0 ]; then
          echo "    indexing (per-mailbox + global)..."
          PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
            --mailbox "$MAILBOX" \
            --data-root "$DATA_ROOT" \
            --global-index "$GLOBAL_INDEX_DIR/mail_index.sqlite" \
            || { echo "ERROR: index-mailbox failed for $MAILBOX"; exit 1; }
          echo "    indexing done."
        else
          echo "    [skip-index] skipping indexing for $MAILBOX"
        fi

        echo "    $MAILBOX: done"
      done

      echo ""
      echo "==> [sync-all] All mailboxes processed."
      echo "    Global index: $GLOBAL_INDEX_DIR/mail_index.sqlite"
    '';
```

**Step 2: Update enterShell welcome message**

Replace the old welcome message in `enterShell` with:

```nix
  enterShell = ''
    echo ""
    echo "  mailbox-archive devenv"
    echo "  ──────────────────────────────────────────────────────"
    echo "  sync-all                       rsync + extract + index all mailboxes"
    echo "  extract-attachments <mailbox>  extract MIME attachments for one mailbox"
    echo "  index-mailbox <mailbox>        (re)build per-mailbox SQLite index"
    echo "  index-all                      (re)build global index across all mailboxes"
    echo "  db-start                       start local MariaDB dev server"
    echo "  db-migrate                     run SQL migrations"
    echo "  search-archive <query>         full-text search across archived emails"
    echo "  ──────────────────────────────────────────────────────"
    echo "  data    : $DEVENV_ROOT/data/"
    echo "  index   : $DEVENV_ROOT/data/index/mail_index.sqlite"
    echo "  logs    : $DEVENV_ROOT/logs/"
    echo ""
  '';
```

**Step 3: Verify devenv.nix parses**

```bash
nix-instantiate --parse devenv.nix 2>&1 | head -5
```

Expected: no parse errors.

**Step 4: Test the command**

```bash
devenv shell -- sync-all --help
```

Expected: prints usage.

**Step 5: Commit**

```bash
git add devenv.nix
git commit -m "feat(devenv): add sync-all command with rsync+extract+index pipeline"
```

---

## Task 7: Implement CC + body extraction in parser.py

**Files:**
- Modify: `src/maildir_report/parser.py`

Depends on: Task 5 (red tests must exist and fail). After this task, those tests must pass.

The `parse_email_file()` function currently returns a dict WITHOUT `cc_addrs` and `body_text`. We add both.

**Step 1: Add `cc_addrs` extraction**

In `parser.py`, in the `parse_email_file()` function, at line 191 (after `to = _decode_header_str(msg.get("To", ""))`), add:

```python
    cc = _decode_header_str(msg.get("Cc", ""))
```

**Step 2: Add `body_text` extraction**

After the `msg.walk()` loop ends (after `sorted_parts = sort_parts(raw_parts)`, around line 276), add a second pass to extract the plain-text body. Add a helper function `_extract_body_text()` before `parse_email_file()`:

```python
def _extract_body_text(msg: _emsg.Message) -> str:
    """Extract the first text/plain body part as a decoded unicode string.

    Charset fallback chain: declared charset → utf-8 → latin-1 (errors='replace').
    Returns empty string if no text/plain part exists.
    """
    for part in msg.walk():
        if part.get_content_type() != "text/plain":
            continue
        if part.get_filename():
            # Named text/plain parts are attachments, not body text.
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, (bytes, bytearray)) or not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            try:
                return payload.decode("utf-8", errors="replace")
            except Exception:
                return payload.decode("latin-1", errors="replace")
    return ""
```

**Step 3: Add `body_text` call in `parse_email_file()`**

After `sorted_parts = sort_parts(raw_parts)` (line 276), add:

```python
    body_text = _extract_body_text(msg)
```

**Step 4: Add both fields to the returned record dict**

In the `record` dict assembly (around line 279), add `cc_addrs` and `body_text`:

```python
    record: dict[str, Any] = {
        "filepath": filepath,
        "message_id": message_id,
        "subject": subject,
        "date": date_fmt,
        "date_day": date_day,
        "sender": sender,
        "to": to,
        "cc_addrs": cc,       # NEW
        "body_text": body_text, # NEW
        "folder": folder,
        "total_size": len(raw),
        "parts": sorted_parts,
        "has_nested_messages": has_nested_messages,
        "dup_group_id": None,
        "dup_rank": None,
    }
```

**Step 5: Run the red tests — they must now pass**

```bash
pytest tests/test_body_cc_extraction.py -v
```

Expected: ALL pass.

**Step 6: Run full test suite**

```bash
pytest -q
```

Expected: all tests pass.

**Step 7: Commit**

```bash
git add src/maildir_report/parser.py
git commit -m "feat(parser): extract cc_addrs and body_text from email messages"
```

---

## Task 8: Write red tests for SQLite schema v2 migration

**Files:**
- Create: `tests/test_index_schema_migration_v2.py`

These tests FAIL until Task 9 implements the schema upgrade in `index_mailbox.py`.

The schema upgrade adds 3 new columns (`to_addrs`, `cc_addrs`, `body_text`) to the `emails` table and tracks schema version via `PRAGMA user_version`.

**Step 1: Write the test file**

Create `tests/test_index_schema_migration_v2.py`:

```python
"""
test_index_schema_migration_v2.py — Red tests for SQLite schema v2 migration.

Tests FAIL until Task 9 upgrades index_mailbox.py to schema v2.

Schema v2 adds to_addrs, cc_addrs, body_text columns and PRAGMA user_version=2.
Migration: existing databases at user_version=0 or 1 are upgraded in-place
via ALTER TABLE ADD COLUMN (non-destructive).
"""
import pathlib
import sqlite3
from email.mime.text import MIMEText

import pytest

from maildir_report.index_mailbox import _init_db, index_mailbox, IndexResult


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_maildir(base: pathlib.Path) -> pathlib.Path:
    root = base / ".maildir"
    for sub in ("cur", "new", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _simple_mail(
    subject: str = "Test",
    from_: str = "alice@example.com",
    to: str = "bob@example.com",
    cc: str = "",
    body: str = "Hello.",
    message_id: str = "<t1@x>",
) -> bytes:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["Message-ID"] = message_id
    return msg.as_bytes()


def _make_data_root(tmp_path: pathlib.Path, mailbox: str = "test_mb"):
    data_root = tmp_path / "data" / "mailboxes" / mailbox
    maildir_root = data_root / "maildir" / ".maildir"
    for sub in ("cur", "new", "tmp"):
        (maildir_root / sub).mkdir(parents=True, exist_ok=True)
    (data_root / "attachments").mkdir(parents=True, exist_ok=True)
    return data_root, maildir_root


# ── schema v2 column tests ────────────────────────────────────────────────────

def test_emails_table_has_to_addrs_column(tmp_path):
    """emails table must have a to_addrs column after _init_db."""
    db_path = tmp_path / "test.sqlite"
    conn = _init_db(db_path)
    conn.close()
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)")}
    conn.close()
    assert "to_addrs" in cols, "emails table missing 'to_addrs' column"


def test_emails_table_has_cc_addrs_column(tmp_path):
    """emails table must have a cc_addrs column after _init_db."""
    db_path = tmp_path / "test.sqlite"
    conn = _init_db(db_path)
    conn.close()
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)")}
    conn.close()
    assert "cc_addrs" in cols, "emails table missing 'cc_addrs' column"


def test_emails_table_has_body_text_column(tmp_path):
    """emails table must have a body_text column after _init_db."""
    db_path = tmp_path / "test.sqlite"
    conn = _init_db(db_path)
    conn.close()
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)")}
    conn.close()
    assert "body_text" in cols, "emails table missing 'body_text' column"


def test_user_version_is_2(tmp_path):
    """PRAGMA user_version must equal 2 after _init_db."""
    db_path = tmp_path / "test.sqlite"
    conn = _init_db(db_path)
    conn.close()
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 2, f"Expected PRAGMA user_version=2, got {version}"


def test_upsert_stores_to_addrs(tmp_path):
    """index_mailbox() must store to_addrs in the emails table."""
    data_root, maildir_root = _make_data_root(tmp_path)
    raw = _simple_mail(to="bob@example.com, charlie@example.com", message_id="<to1@x>")
    (maildir_root / "cur" / "1.to.msg").write_bytes(raw)

    result = index_mailbox("test_mb", str(data_root))
    assert result.emails_indexed == 1

    db_path = data_root / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT to_addrs FROM emails LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert "bob@example.com" in row[0]


def test_upsert_stores_cc_addrs(tmp_path):
    """index_mailbox() must store cc_addrs in the emails table."""
    data_root, maildir_root = _make_data_root(tmp_path)
    raw = _simple_mail(cc="carol@example.com", message_id="<cc1@x>")
    (maildir_root / "cur" / "1.cc.msg").write_bytes(raw)

    result = index_mailbox("test_mb", str(data_root))
    assert result.emails_indexed == 1

    db_path = data_root / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT cc_addrs FROM emails LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert "carol@example.com" in row[0]


def test_upsert_stores_body_text(tmp_path):
    """index_mailbox() must store body_text in the emails table."""
    data_root, maildir_root = _make_data_root(tmp_path)
    raw = _simple_mail(body="Archive body content.", message_id="<body1@x>")
    (maildir_root / "cur" / "1.body.msg").write_bytes(raw)

    result = index_mailbox("test_mb", str(data_root))
    assert result.emails_indexed == 1

    db_path = data_root / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT body_text FROM emails LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert "Archive body content." in row[0]


def test_migration_from_v1_schema(tmp_path):
    """An existing v1 database (missing new columns) must be upgraded in-place."""
    db_path = tmp_path / "v1.sqlite"

    # Simulate a v1 database: create emails without new columns, user_version=0
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE emails (
            mailbox           TEXT NOT NULL,
            stable_id         TEXT NOT NULL PRIMARY KEY,
            filepath          TEXT NOT NULL,
            folder            TEXT NOT NULL,
            date              TEXT NOT NULL,
            from_addr         TEXT NOT NULL,
            subject           TEXT NOT NULL,
            total_size_bytes  INTEGER NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO emails VALUES
        ('mb', 'aaa111', '/path/a', 'INBOX', '2024-01-01', 'x@x', 'Sub', 100)
    """)
    conn.execute("PRAGMA user_version=1;")
    conn.commit()
    conn.close()

    # _init_db must detect v1 and upgrade
    conn = _init_db(db_path)
    conn.close()

    # Verify new columns exist and existing row is preserved
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)")}
    rows = conn.execute("SELECT stable_id FROM emails").fetchall()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    assert "to_addrs" in cols
    assert "cc_addrs" in cols
    assert "body_text" in cols
    assert len(rows) == 1, "Existing data must be preserved during migration"
    assert version == 2


def test_wal_checkpoint_runs_without_error(tmp_path):
    """WAL checkpoint must complete without raising after index_mailbox."""
    data_root, maildir_root = _make_data_root(tmp_path)
    raw = _simple_mail(message_id="<wal1@x>")
    (maildir_root / "cur" / "1.wal.msg").write_bytes(raw)

    # Should not raise
    result = index_mailbox("test_mb", str(data_root))
    assert result.emails_indexed == 1

    # Verify WAL checkpoint happened (WAL file should be very small or absent)
    wal_file = data_root / "index.sqlite-wal"
    if wal_file.exists():
        # After checkpoint, WAL should be minimal
        assert wal_file.stat().st_size < 64 * 1024, "WAL should be checkpointed"
```

**Step 2: Run to verify tests FAIL**

```bash
pytest tests/test_index_schema_migration_v2.py -v
```

Expected: ALL tests fail (missing columns, wrong user_version, etc.).

**Step 3: Commit**

```bash
git add tests/test_index_schema_migration_v2.py
git commit -m "test(index): red tests for SQLite schema v2 migration (to_addrs, cc_addrs, body_text)"
```

---

## Task 9: Upgrade index_mailbox.py schema to v2

**Files:**
- Modify: `src/maildir_report/index_mailbox.py`

Depends on: Task 7 (parser returns `cc_addrs`, `body_text`), Task 8 (red tests exist).

Changes:
1. Update `_CREATE_EMAILS` DDL to add `to_addrs`, `cc_addrs`, `body_text` columns
2. Add `PRAGMA user_version` to schema tracking (v2)
3. Add migration logic in `_init_db()`: detect old schema and `ALTER TABLE ADD COLUMN`
4. Update `_upsert_email()` to insert the 3 new fields
5. Add WAL checkpoint call after bulk indexing in `index_mailbox()`

**Step 1: Update `_CREATE_EMAILS` DDL**

Replace lines 95–106:
```python
_CREATE_EMAILS = """
CREATE TABLE IF NOT EXISTS emails (
    mailbox           TEXT    NOT NULL,
    stable_id         TEXT    NOT NULL PRIMARY KEY,
    filepath          TEXT    NOT NULL,
    folder            TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    from_addr         TEXT    NOT NULL,
    subject           TEXT    NOT NULL,
    total_size_bytes  INTEGER NOT NULL
);
"""
```

With:
```python
_SCHEMA_VERSION = 2

_CREATE_EMAILS = """
CREATE TABLE IF NOT EXISTS emails (
    mailbox           TEXT    NOT NULL,
    stable_id         TEXT    NOT NULL PRIMARY KEY,
    filepath          TEXT    NOT NULL,
    folder            TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    from_addr         TEXT    NOT NULL,
    to_addrs          TEXT    NOT NULL DEFAULT '',
    cc_addrs          TEXT    NOT NULL DEFAULT '',
    subject           TEXT    NOT NULL,
    body_text         TEXT    NOT NULL DEFAULT '',
    total_size_bytes  INTEGER NOT NULL
);
"""
```

**Step 2: Update `_init_db()` to handle migration**

Replace the `_init_db()` function body (lines 171–183) with:

```python
def _init_db(db_path: pathlib.Path) -> sqlite3.Connection:
    """Open (or create) a SQLite database and ensure the schema exists.

    Performs in-place migration from v1 (user_version < 2) to v2:
    adds to_addrs, cc_addrs, body_text columns via ALTER TABLE ADD COLUMN.
    Existing rows are preserved; new columns default to ''.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    # Create tables (no-op if they already exist).
    conn.execute(_CREATE_EMAILS)
    conn.execute(_CREATE_ATTACHMENTS)
    for idx_sql in _CREATE_INDEXES:
        conn.execute(idx_sql)

    # Schema migration: add new columns if missing.
    existing_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if existing_version < _SCHEMA_VERSION:
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(emails)")
        }
        for col, default in [
            ("to_addrs", "''"),
            ("cc_addrs", "''"),
            ("body_text", "''"),
        ]:
            if col not in existing_cols:
                conn.execute(
                    f"ALTER TABLE emails ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
                )
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION};")

    conn.commit()
    return conn
```

**Step 3: Update `_upsert_email()` to include the 3 new fields**

Replace the `_upsert_email()` function (lines 186–208):

```python
def _upsert_email(
    conn: sqlite3.Connection,
    mailbox: str,
    email_rec: dict[str, Any],
) -> None:
    """INSERT OR REPLACE one email row."""
    conn.execute(
        """
        INSERT OR REPLACE INTO emails
            (mailbox, stable_id, filepath, folder, date, from_addr,
             to_addrs, cc_addrs, subject, body_text, total_size_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mailbox,
            email_rec["stable_id"],
            email_rec.get("filepath", ""),
            email_rec.get("folder", ""),
            email_rec.get("date", ""),
            email_rec.get("sender", ""),
            email_rec.get("to", ""),
            email_rec.get("cc_addrs", ""),
            email_rec.get("subject", ""),
            email_rec.get("body_text", ""),
            email_rec.get("total_size", 0),
        ),
    )
```

**Step 4: Add WAL checkpoint after bulk indexing**

In `index_mailbox()`, after `conn.commit()` and before `finally:` (after line 341), add:

```python
        # WAL checkpoint: flush WAL to main DB file after bulk write.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        if global_conn:
            global_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
```

**Step 5: Run the red tests — they must now pass**

```bash
pytest tests/test_index_schema_migration_v2.py -v
```

Expected: ALL pass.

**Step 6: Run full test suite**

```bash
pytest -q
```

Expected: all tests pass.

**Step 7: Commit**

```bash
git add src/maildir_report/index_mailbox.py
git commit -m "feat(index): schema v2 — add to_addrs, cc_addrs, body_text; add WAL checkpoint; v1 migration"
```

---

## Task 10: Write and implement `import_archive.php`

**Files:**
- Create: `web/src/cli/import_archive.php`

Depends on: Task 2 (MySQL archive schema), Task 9 (SQLite has the new columns).

This script reads the global SQLite index (`data/index/mail_index.sqlite` or a per-mailbox `index.sqlite`) and upserts all rows into MySQL `archive_emails` and `archive_attachments` tables. Chunk size: 5000 rows per transaction.

Pattern: follows `web/src/cli/migrate.php` for config loading and PDO setup.

**Step 1: Write the script**

Create `web/src/cli/import_archive.php`:

```php
<?php
/**
 * web/src/cli/import_archive.php — Import SQLite archive index into MySQL.
 *
 * Reads from a SQLite index file (global or per-mailbox) and upserts all
 * rows into MySQL archive_emails and archive_attachments tables.
 *
 * Designed to run after index-mailbox / index-all. Idempotent (uses
 * INSERT ... ON DUPLICATE KEY UPDATE). Chunk size: 5000 rows.
 *
 * Usage:
 *   php web/src/cli/import_archive.php \
 *     --sqlite <path/to/mail_index.sqlite> \
 *     [--config <path/to/local.php>] \
 *     [--socket <mariadb-socket>] \
 *     [--chunk <n>] \
 *     [--quiet]
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "This script must be run from the command line.\n");
    exit(1);
}

// ── Parse args ───────────────────────────────────────────────────────────────
$opts = getopt('', ['sqlite:', 'config:', 'socket:', 'chunk:', 'quiet', 'help', 'h']);

if (isset($opts['help']) || isset($opts['h'])) {
    fwrite(STDOUT, <<<USAGE
    Usage: php web/src/cli/import_archive.php [OPTIONS]

      Import a SQLite archive index into MySQL archive_emails and
      archive_attachments tables. Idempotent: safe to run multiple times.

    Options:
      --sqlite <path>   Path to SQLite index file (required)
                        Use data/index/mail_index.sqlite for global index
      --config <path>   Path to local.php config (default: web/config/local.php)
      --socket <path>   MariaDB Unix socket (overrides config)
      --chunk <n>       Rows per transaction (default: 5000)
      --quiet           Suppress progress output
      --help            Show this message and exit

    Exit codes:
      0  Success
      1  Error

    USAGE);
    exit(0);
}

if (empty($opts['sqlite'])) {
    fwrite(STDERR, "ERROR: --sqlite <path> is required.\n");
    fwrite(STDERR, "Run: php web/src/cli/import_archive.php --help\n");
    exit(1);
}

$sqlitePath = $opts['sqlite'];
$chunkSize  = (int)($opts['chunk'] ?? 5000);
$quiet      = isset($opts['quiet']);

if (!is_file($sqlitePath)) {
    fwrite(STDERR, "ERROR: SQLite file not found: $sqlitePath\n");
    exit(1);
}

// ── Load config ──────────────────────────────────────────────────────────────
$scriptDir  = dirname(__DIR__, 2); // web/
$configPath = $opts['config'] ?? ($scriptDir . '/config/local.php');

if (!is_file($configPath)) {
    fwrite(STDERR, "ERROR: Config not found: $configPath\n");
    fwrite(STDERR, "  Copy web/config/local.php.example -> web/config/local.php\n");
    exit(1);
}

/** @var array<string,mixed> $config */
$config = require $configPath;
$dbCfg  = $config['db'] ?? [];

$socket = $opts['socket'] ?? $dbCfg['socket'] ?? (getenv('DEVENV_STATE') . '/mysql.sock');

// ── Connect to MySQL ─────────────────────────────────────────────────────────
if ($socket && file_exists($socket)) {
    $dsn = "mysql:unix_socket=$socket;dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
} else {
    $host = $dbCfg['host'] ?? '127.0.0.1';
    $port = $dbCfg['port'] ?? 3306;
    $dsn  = "mysql:host=$host;port=$port;dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
}

try {
    $pdo = new PDO($dsn, $dbCfg['user'] ?? '', $dbCfg['password'] ?? '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES   => false,
    ]);
} catch (PDOException $e) {
    fwrite(STDERR, "ERROR: MySQL connection failed: " . $e->getMessage() . "\n");
    exit(1);
}

// ── Connect to SQLite ────────────────────────────────────────────────────────
try {
    $sqlite = new PDO('sqlite:' . $sqlitePath, '', '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (PDOException $e) {
    fwrite(STDERR, "ERROR: SQLite open failed: " . $e->getMessage() . "\n");
    exit(1);
}

// ── Prepared statements ──────────────────────────────────────────────────────
$upsertEmail = $pdo->prepare(<<<SQL
    INSERT INTO archive_emails
        (mailbox, stable_id, filepath, folder, date, from_addr,
         to_addrs, cc_addrs, subject, body_text, total_size_bytes)
    VALUES
        (:mailbox, :stable_id, :filepath, :folder, :date, :from_addr,
         :to_addrs, :cc_addrs, :subject, :body_text, :total_size_bytes)
    ON DUPLICATE KEY UPDATE
        filepath         = VALUES(filepath),
        folder           = VALUES(folder),
        date             = VALUES(date),
        from_addr        = VALUES(from_addr),
        to_addrs         = VALUES(to_addrs),
        cc_addrs         = VALUES(cc_addrs),
        subject          = VALUES(subject),
        body_text        = VALUES(body_text),
        total_size_bytes = VALUES(total_size_bytes)
SQL);

$upsertAttachment = $pdo->prepare(<<<SQL
    INSERT INTO archive_attachments
        (mailbox, email_stable_id, stored_path, sha256, size, mime, original_filename)
    VALUES
        (:mailbox, :email_stable_id, :stored_path, :sha256, :size, :mime, :original_filename)
    ON DUPLICATE KEY UPDATE
        stored_path       = VALUES(stored_path),
        size              = VALUES(size),
        mime              = VALUES(mime),
        original_filename = VALUES(original_filename)
SQL);

// ── Import emails ────────────────────────────────────────────────────────────
$totalEmails = (int)$sqlite->query("SELECT COUNT(*) FROM emails")->fetchColumn();
if (!$quiet) {
    fwrite(STDOUT, "==> Importing $totalEmails emails from $sqlitePath\n");
}

$emailsImported = 0;
$offset = 0;

while ($offset < $totalEmails) {
    $rows = $sqlite->query(
        "SELECT mailbox, stable_id, filepath, folder, date, from_addr,
                to_addrs, cc_addrs, subject, body_text, total_size_bytes
         FROM emails
         LIMIT $chunkSize OFFSET $offset"
    )->fetchAll();

    if (empty($rows)) {
        break;
    }

    $pdo->beginTransaction();
    try {
        foreach ($rows as $row) {
            $upsertEmail->execute([
                ':mailbox'          => $row['mailbox'],
                ':stable_id'        => $row['stable_id'],
                ':filepath'         => $row['filepath'],
                ':folder'           => $row['folder'],
                ':date'             => $row['date'],
                ':from_addr'        => $row['from_addr'],
                ':to_addrs'         => $row['to_addrs'] ?? '',
                ':cc_addrs'         => $row['cc_addrs'] ?? '',
                ':subject'          => $row['subject'],
                ':body_text'        => $row['body_text'] ?? '',
                ':total_size_bytes' => (int)($row['total_size_bytes'] ?? 0),
            ]);
        }
        $pdo->commit();
    } catch (PDOException $e) {
        $pdo->rollBack();
        fwrite(STDERR, "ERROR: Email import failed at offset $offset: " . $e->getMessage() . "\n");
        exit(1);
    }

    $emailsImported += count($rows);
    $offset += $chunkSize;

    if (!$quiet) {
        fwrite(STDOUT, "  emails: $emailsImported / $totalEmails\n");
    }
}

// ── Import attachments ────────────────────────────────────────────────────────
$totalAtts = (int)$sqlite->query("SELECT COUNT(*) FROM attachments")->fetchColumn();
if (!$quiet) {
    fwrite(STDOUT, "==> Importing $totalAtts attachments\n");
}

// We need mailbox for each attachment — join emails table
$attsImported = 0;
$offset = 0;

while ($offset < $totalAtts) {
    $rows = $sqlite->query(
        "SELECT e.mailbox, a.email_stable_id, a.stored_path,
                a.sha256, a.size, a.mime, a.original_filename
         FROM attachments a
         JOIN emails e ON e.stable_id = a.email_stable_id
         LIMIT $chunkSize OFFSET $offset"
    )->fetchAll();

    if (empty($rows)) {
        break;
    }

    $pdo->beginTransaction();
    try {
        foreach ($rows as $row) {
            $upsertAttachment->execute([
                ':mailbox'           => $row['mailbox'],
                ':email_stable_id'   => $row['email_stable_id'],
                ':stored_path'       => $row['stored_path'],
                ':sha256'            => $row['sha256'],
                ':size'              => (int)($row['size'] ?? 0),
                ':mime'              => $row['mime'],
                ':original_filename' => $row['original_filename'],
            ]);
        }
        $pdo->commit();
    } catch (PDOException $e) {
        $pdo->rollBack();
        fwrite(STDERR, "ERROR: Attachment import failed at offset $offset: " . $e->getMessage() . "\n");
        exit(1);
    }

    $attsImported += count($rows);
    $offset += $chunkSize;

    if (!$quiet) {
        fwrite(STDOUT, "  attachments: $attsImported / $totalAtts\n");
    }
}

if (!$quiet) {
    fwrite(STDOUT, "==> Done. emails=$emailsImported attachments=$attsImported\n");
}
exit(0);
```

**Step 2: Verify PHP syntax**

```bash
php -l web/src/cli/import_archive.php
```

Expected: `No syntax errors detected`.

**Step 3: Run help to verify it executes**

```bash
php web/src/cli/import_archive.php --help
```

Expected: prints usage without error.

**Step 4: Commit**

```bash
git add web/src/cli/import_archive.php
git commit -m "feat(php): add import_archive.php — SQLite→MySQL chunked upsert"
```

---

## Task 11: Write QA script

**Files:**
- Create: `web/scripts/qa-archive.sh`

Depends on: Tasks 6, 9, 10 (all pipeline pieces exist).

This script creates a synthetic mailbox, runs the full pipeline end-to-end, and verifies each step produced the expected output.

**Step 1: Write the script**

Create `web/scripts/qa-archive.sh`:

```bash
#!/usr/bin/env bash
# web/scripts/qa-archive.sh — End-to-end QA for the mailbox archive pipeline.
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
  "$DATA_ROOT/maildir/.maildir" "$ATTACHMENTS" \
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
SOCK="${DEVENV_STATE:-}/mysql.sock"
php "$DEVENV_ROOT/web/src/cli/import_archive.php" \
  --sqlite "$DATA_ROOT/index.sqlite" \
  --socket "$SOCK" \
  ${QUIET:+--quiet} \
  || fail "import_archive.php failed"

MYSQL_COUNT=$(mysql -u mailreview --socket="$SOCK" mailreview \
  -e "SELECT COUNT(*) FROM archive_emails WHERE mailbox='$QA_MAILBOX';" \
  --skip-column-names 2>/dev/null)
[ "${MYSQL_COUNT:-0}" -ge 1 ] && pass "emails in MySQL: $MYSQL_COUNT" || fail "No emails in archive_emails"

ATT_MYSQL=$(mysql -u mailreview --socket="$SOCK" mailreview \
  -e "SELECT COUNT(*) FROM archive_attachments WHERE mailbox='$QA_MAILBOX';" \
  --skip-column-names 2>/dev/null)
[ "${ATT_MYSQL:-0}" -ge 1 ] && pass "attachments in MySQL: $ATT_MYSQL" || fail "No attachments in archive_attachments"

log "==> Step 4: Verify FULLTEXT search"
FT_RESULT=$(mysql -u mailreview --socket="$SOCK" mailreview \
  -e "SELECT stable_id FROM archive_emails WHERE MATCH(subject,from_addr,to_addrs,cc_addrs,body_text) AGAINST('QA body text' IN BOOLEAN MODE) AND mailbox='$QA_MAILBOX';" \
  --skip-column-names 2>/dev/null)
[ -n "$FT_RESULT" ] && pass "FULLTEXT search returned results" || fail "FULLTEXT search returned no results"

log "==> Cleanup"
mysql -u mailreview --socket="$SOCK" mailreview \
  -e "DELETE FROM archive_emails WHERE mailbox='$QA_MAILBOX'; DELETE FROM archive_attachments WHERE mailbox='$QA_MAILBOX';" \
  2>/dev/null && pass "MySQL cleanup done" || true
rm -rf "$DATA_ROOT" && pass "synthetic mailbox removed" || true

echo ""
echo "==> [qa-archive] ALL CHECKS PASSED"
```

**Step 2: Make executable**

```bash
chmod +x web/scripts/qa-archive.sh
```

**Step 3: Verify it runs (dry check — needs db-start)**

```bash
bash -n web/scripts/qa-archive.sh
```

Expected: no bash syntax errors.

**Step 4: Commit**

```bash
git add web/scripts/qa-archive.sh
git commit -m "test(qa): add qa-archive.sh end-to-end pipeline QA script"
```

---

## Task 12: Add `search-archive` devenv command

**Files:**
- Modify: `devenv.nix` (add `search-archive` script block)

The command queries MySQL `archive_emails` using `MATCH ... AGAINST` (FULLTEXT) and prints results to the terminal. Uses `mysql` CLI tool directly.

**Step 1: Add search-archive script to devenv.nix**

Add after the `sync-all` command block (before `enterShell`):

```nix
    # ── search-archive: full-text search across archived emails ──────────
    search-archive.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: search-archive <query> [--mailbox <name>] [--limit <n>]"
        echo ""
        echo "  Full-text search across archived emails in MySQL."
        echo "  Uses MariaDB FULLTEXT MATCH...AGAINST on subject, from_addr,"
        echo "  to_addrs, cc_addrs, body_text."
        echo ""
        echo "Options:"
        echo "  --mailbox <name>   Restrict search to one mailbox"
        echo "  --limit <n>        Max results (default: 20)"
        echo "  --help             Show this help message and exit"
        exit 0
      fi
      if [ -z "$1" ]; then
        echo "ERROR: search query required"
        echo "Run: search-archive --help"
        exit 1
      fi

      QUERY="$1"
      shift
      MAILBOX_FILTER=""
      LIMIT=20
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox) MAILBOX_FILTER="$2"; shift 2 ;;
          --limit)   LIMIT="$2"; shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      SOCK="$DEVENV_STATE/mysql.sock"
      WHERE="MATCH(subject, from_addr, to_addrs, cc_addrs, body_text) AGAINST('$QUERY' IN BOOLEAN MODE)"
      if [ -n "$MAILBOX_FILTER" ]; then
        WHERE="$WHERE AND mailbox = '$MAILBOX_FILTER'"
      fi

      echo "==> Searching archive for: $QUERY"
      mysql -u mailreview --socket="$SOCK" mailreview \
        --table \
        -e "SELECT mailbox, date, from_addr, subject,
                   LEFT(body_text, 120) AS body_preview
            FROM archive_emails
            WHERE $WHERE
            ORDER BY date DESC
            LIMIT $LIMIT;" \
        || { echo "ERROR: search failed. Is db-start running?"; exit 1; }
    '';
```

**Step 2: Verify devenv.nix parses**

```bash
nix-instantiate --parse devenv.nix 2>&1 | head -5
```

Expected: no parse errors.

**Step 3: Test help**

```bash
devenv shell -- search-archive --help
```

Expected: prints usage.

**Step 4: Commit**

```bash
git add devenv.nix
git commit -m "feat(devenv): add search-archive terminal FULLTEXT search command"
```

---

## Final: Verification Wave

**Step F1: Run full test suite**

```bash
pytest -q
```

Expected: all tests pass. Note the count of passing tests — should include `test_body_cc_extraction.py` (10 tests) and `test_index_schema_migration_v2.py` (8 tests) as newly passing.

**Step F2: Run QA script**

```bash
db-start
db-migrate
bash web/scripts/qa-archive.sh
```

Expected: `ALL CHECKS PASSED`.

**Step F3: Verify no dead imports**

```bash
python3 -c "
from maildir_report.parser import parse_email_file, scan_maildir
from maildir_report.index_mailbox import index_mailbox, IndexResult
from maildir_report.extract_attachments import extract_attachments
print('OK')
"
```

Expected: `OK`.

**Step F4: Verify devenv commands load**

```bash
devenv shell -- sync-all --help
devenv shell -- extract-attachments --help
devenv shell -- search-archive --help
devenv shell -- index-mailbox --help
devenv shell -- index-all --help
```

Expected: all print usage without error.

**Step F5: Check PHP files lint cleanly**

```bash
php -l web/src/cli/import_archive.php
php -l web/src/cli/migrate.php
```

Expected: `No syntax errors detected` for both.

**Step F6: Final commit**

```bash
git add -A
git status  # should be clean or show only qa-archive.sh chmod change
git diff --stat HEAD  # confirm no unintended changes
```

If clean:
```bash
git log --oneline -12
```

Expected: clean git history with one commit per task.

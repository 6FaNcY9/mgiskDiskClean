# Mailbox Archive — Download, Index, Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download all mrija.org mailboxes to local storage, index emails + attachments into SQLite and MySQL, and provide a terminal search command — all read-only against the server.

**Architecture:** `sync-all` orchestrates rsync → attachment extraction → SQLite indexing for each mailbox, then imports the global SQLite into MariaDB. Parser gets CC + body_text extraction. SQLite schema migrates in-place (v1 → v2). MySQL archive tables with FULLTEXT index power the `search-archive` command.

**Tech Stack:** Python 3.11, SQLite3, MariaDB/MySQL, PHP 8.3, pytest, devenv/Nix, rsync

---

## File Map

**Create:**
- `tests/test_body_cc_extraction.py` — red tests for parser CC + body_text
- `tests/test_index_schema_migration_v2.py` — red tests for SQLite v1→v2 migration
- `tests/fixtures/mailboxes.txt` — fixture mailbox list for QA
- `tests/fixtures/src/qa_test_mailbox/.maildir/cur/001.eml` — plain-text fixture email
- `tests/fixtures/src/qa_test_mailbox/.maildir/cur/002.eml` — fixture email with attachment
- `tests/fixtures/src/qa_test_mailbox/.maildir/new/` — empty dir
- `tests/fixtures/src/qa_test_mailbox/.maildir/tmp/` — empty dir
- `web/migrations/001_archive_schema.sql` — archive_emails + archive_attachments tables
- `web/src/cli/import_archive.php` — SQLite → MySQL importer
- `web/src/cli/search_archive.php` — MySQL FULLTEXT search CLI
- `web/scripts/qa-archive.sh` — end-to-end QA script

**Modify:**
- `src/maildir_report/parser.py` — add `cc_addrs` + `body_text` extraction
- `src/maildir_report/models.py` — add `cc_addrs`, `body_text` to EmailRecord
- `src/maildir_report/index_mailbox.py` — new columns + v1→v2 migration + WAL checkpoint
- `devenv.nix` — remove old scripts; add `sync-all`, `extract-attachments`, `search-archive`
- `pyproject.toml` — remove `reportlab`, `imap-tools` deps

**Delete:**
- `src/maildir_report/apply_decisions.py`
- `src/maildir_report/decisions_template.py`
- `src/maildir_report/pdf.py`
- `src/maildir_report/manifest.py`
- `src/maildir_report/imap_ingest.py`
- `src/maildir_report/pre_store_dedup.py`
- `src/maildir_report/cli.py`
- `src/maildir_report/__main__.py`
- `tests/test_apply_decisions.py`
- `tests/test_decisions_template.py`
- `tests/test_pdf_german_headers.py`
- `tests/test_imap_ingest.py`
- `tests/test_pre_store_dedup.py`
- `tests/test_e2e_cli.py`
- `web/src/Services/ReviewService.php`
- `web/src/Import/Importer.php`
- `web/src/Import/ImportException.php`
- `web/public/login.php`
- `web/public/index.php`
- `web/migrations/001_initial_schema.sql`
- `web/scripts/qa-task5-auth.sh`
- `web/scripts/qa-task5-csrf.sh`
- `web/scripts/qa-task6-csrf-ui-method.sh`
- `web/scripts/qa-task6-ui.sh`

---

## Task 1: Cleanup — Remove Old Files

**Files:** All files listed in the Delete section above.

- [ ] **Step 1: Delete old Python modules**

```bash
git rm src/maildir_report/apply_decisions.py \
       src/maildir_report/decisions_template.py \
       src/maildir_report/pdf.py \
       src/maildir_report/manifest.py \
       src/maildir_report/imap_ingest.py \
       src/maildir_report/pre_store_dedup.py \
       src/maildir_report/cli.py \
       src/maildir_report/__main__.py
```

- [ ] **Step 2: Delete old test files**

```bash
git rm tests/test_apply_decisions.py \
       tests/test_decisions_template.py \
       tests/test_pdf_german_headers.py \
       tests/test_imap_ingest.py \
       tests/test_pre_store_dedup.py \
       tests/test_e2e_cli.py
```

- [ ] **Step 3: Delete old web files**

```bash
git rm web/src/Services/ReviewService.php \
       web/src/Import/Importer.php \
       web/src/Import/ImportException.php \
       web/public/login.php \
       web/public/index.php \
       web/migrations/001_initial_schema.sql \
       web/scripts/qa-task5-auth.sh \
       web/scripts/qa-task5-csrf.sh \
       web/scripts/qa-task6-csrf-ui-method.sh \
       web/scripts/qa-task6-ui.sh
```

- [ ] **Step 4: Verify remaining test suite still loads**

```bash
PYTHONPATH=src python -m pytest tests/ --collect-only -q 2>&1 | head -20
```

Expected: no `ModuleNotFoundError` for removed modules. Some tests may still fail — that is acceptable at this stage.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove review workflow — replaced by archive pipeline"
```

---

## Task 2: Update pyproject.toml

**Files:** Modify `pyproject.toml`

- [ ] **Step 1: Remove `reportlab` and `imap-tools` deps**

Open `pyproject.toml`. The current content has:

```toml
dependencies = [
    "reportlab>=4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
]
imap = [
    "imap-tools>=1.6",
]
```

Replace the entire `[project]` dependencies and optional-dependencies block with:

```toml
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
]
```

- [ ] **Step 2: Verify package installs without reportlab**

```bash
PYTHONPATH=src python -c "import maildir_report; print('ok')"
```

Expected: `ok` (or `ModuleNotFoundError: No module named 'maildir_report'` if `__init__.py` depends on removed modules — fix those imports if needed).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): remove reportlab and imap-tools"
```

---

## Task 3: Write Red Tests — Parser CC + Body Extraction

**Files:** Create `tests/test_body_cc_extraction.py`

- [ ] **Step 1: Create the test file**

```python
# tests/test_body_cc_extraction.py
"""Tests for CC and body_text extraction in parser.parse_email_file.

These tests are RED before Task 4 (parser implementation).
Run: pytest tests/test_body_cc_extraction.py -v
"""
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from maildir_report.parser import parse_email_file


def _write_eml(tmp_path, msg, name="test.eml"):
    p = tmp_path / name
    p.write_bytes(msg.as_bytes())
    return str(p)


def _base_headers(msg, mid="<1@test>"):
    msg["From"] = "sender@x.com"
    msg["To"] = "recipient@x.com"
    msg["Subject"] = "test subject"
    msg["Message-ID"] = mid
    msg["Date"] = "Mon, 1 Jan 2024 10:00:00 +0000"


# ── cc_addrs ──────────────────────────────────────────────────────────────────

def test_cc_header_extracted(tmp_path):
    msg = MIMEText("body text", "plain", "utf-8")
    _base_headers(msg, "<cc1@test>")
    msg["Cc"] = "cc1@x.com, cc2@x.com"
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert rec["cc_addrs"] == "cc1@x.com, cc2@x.com"


def test_cc_header_empty_when_missing(tmp_path):
    msg = MIMEText("body text", "plain", "utf-8")
    _base_headers(msg, "<cc2@test>")
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert rec["cc_addrs"] == ""


# ── body_text ─────────────────────────────────────────────────────────────────

def test_body_text_plain_extracted(tmp_path):
    msg = MIMEText("Hello world body text", "plain", "utf-8")
    _base_headers(msg, "<bt1@test>")
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert "Hello world body text" in rec["body_text"]


def test_body_text_empty_when_no_text_part(tmp_path):
    msg = MIMEMultipart()
    _base_headers(msg, "<bt2@test>")
    att = MIMEApplication(b"pdfbytes", Name="doc.pdf")
    att["Content-Disposition"] = 'attachment; filename="doc.pdf"'
    msg.attach(att)
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert rec["body_text"] == ""


def test_body_text_multipart_alternative(tmp_path):
    """Multipart/alternative: plain text part is preferred over HTML."""
    msg = MIMEMultipart("alternative")
    _base_headers(msg, "<bt3@test>")
    msg.attach(MIMEText("plain version of content", "plain", "utf-8"))
    msg.attach(MIMEText("<b>html version</b>", "html", "utf-8"))
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert "plain version of content" in rec["body_text"]


def test_body_text_windows1251_charset(tmp_path):
    """Cyrillic windows-1251 body decodes without raising."""
    cyrillic = "Привіт архів"
    raw_bytes = cyrillic.encode("windows-1251")
    raw_eml = (
        b"From: a@x.com\r\nTo: b@x.com\r\nSubject: charset\r\n"
        b"Message-ID: <bt4@test>\r\nDate: Mon, 1 Jan 2024 10:00:00 +0000\r\n"
        b"Content-Type: text/plain; charset=windows-1251\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\n"
    ) + raw_bytes
    p = tmp_path / "cyrillic.eml"
    p.write_bytes(raw_eml)
    rec = parse_email_file(str(p), "INBOX")
    assert isinstance(rec["body_text"], str)
    assert len(rec["body_text"]) > 0


def test_body_text_is_string_type(tmp_path):
    msg = MIMEText("some body", "plain", "utf-8")
    _base_headers(msg, "<bt5@test>")
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert isinstance(rec["body_text"], str)
    assert isinstance(rec["cc_addrs"], str)
```

- [ ] **Step 2: Run tests — confirm they fail (red)**

```bash
PYTHONPATH=src python -m pytest tests/test_body_cc_extraction.py -v 2>&1 | tail -20
```

Expected: `KeyError: 'cc_addrs'` or `KeyError: 'body_text'` — tests fail because the keys don't exist yet.

---

## Task 4: Implement CC + Body Extraction in parser.py

**Files:** Modify `src/maildir_report/parser.py`, `src/maildir_report/models.py`

- [ ] **Step 1: Add `cc_addrs` and `body_text` to EmailRecord in models.py**

Open `src/maildir_report/models.py`. Find the `EmailRecord` class. Add two new fields after the existing `to` field:

```python
# existing fields already present:
#   filepath, message_id, subject, date, date_day, sender, to, folder,
#   total_size, parts, stable_id, dup_group_id, dup_rank
cc_addrs: str   # Raw Cc: header string; empty when absent
body_text: str  # Decoded plain-text body; empty when no text/plain part
```

- [ ] **Step 2: Add `cc_addrs` extraction to `parse_email_file`**

Open `src/maildir_report/parser.py`. In `parse_email_file`, find the block that extracts headers (around the `to = _decode_header_str(...)` line):

```python
    to = _decode_header_str(msg.get("To", ""))
```

Immediately after that line, add:

```python
    cc_addrs = _decode_header_str(msg.get("Cc", ""))
```

- [ ] **Step 3: Add `body_text` extraction to `parse_email_file`**

In `parse_email_file`, after the `sorted_parts = sort_parts(raw_parts)` line and before the `record: dict[str, Any] = {` block, add:

```python
    # ── 4b. extract plain-text body ─────────────────────────────────────────
    body_text = ""
    for _part in msg.walk():
        if _part.get_content_type() == "text/plain" and not _part.get_filename():
            _charset = _part.get_content_charset() or "utf-8"
            try:
                _payload = _part.get_payload(decode=True)
                if isinstance(_payload, (bytes, bytearray)):
                    body_text = _payload.decode(_charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                _payload = _part.get_payload(decode=True)
                if isinstance(_payload, (bytes, bytearray)):
                    body_text = _payload.decode("latin-1", errors="replace")
            break
```

- [ ] **Step 4: Add `cc_addrs` and `body_text` to the returned record dict**

In `parse_email_file`, find the `record: dict[str, Any] = {` block. Add the two new keys. The block currently ends around:

```python
        "dup_group_id": None,
        "dup_rank": None,
    }
```

Add before the closing `}`:

```python
        "cc_addrs": cc_addrs,
        "body_text": body_text,
```

- [ ] **Step 5: Run new tests — confirm they pass (green)**

```bash
PYTHONPATH=src python -m pytest tests/test_body_cc_extraction.py -v
```

Expected: all 7 tests `PASSED`.

- [ ] **Step 6: Run full test suite — no regressions**

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

Expected: all existing tests pass. Note the count — it should be the remaining tests after the cleanup in Task 1.

- [ ] **Step 7: Commit**

```bash
git add src/maildir_report/parser.py src/maildir_report/models.py \
        tests/test_body_cc_extraction.py
git commit -m "feat(parser): extract cc_addrs and body_text from email messages"
```

---

## Task 5: Write Red Tests — SQLite Schema Migration v2

**Files:** Create `tests/test_index_schema_migration_v2.py`

- [ ] **Step 1: Create the test file**

```python
# tests/test_index_schema_migration_v2.py
"""Tests for SQLite schema migration from v1 to v2 in index_mailbox.

v1 schema: emails has no to_addrs / cc_addrs / body_text columns, user_version=1.
v2 schema: adds those three columns, user_version=2.

These tests are RED before Task 6.
Run: pytest tests/test_index_schema_migration_v2.py -v
"""
import sqlite3
import pathlib

import pytest

from maildir_report.index_mailbox import _init_db


def _make_v1_db(path: pathlib.Path) -> None:
    """Build a v1 schema SQLite file (no new columns, user_version=1)."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE emails (
            mailbox          TEXT    NOT NULL,
            stable_id        TEXT    NOT NULL PRIMARY KEY,
            filepath         TEXT    NOT NULL,
            folder           TEXT    NOT NULL DEFAULT '',
            date             TEXT    NOT NULL DEFAULT '',
            from_addr        TEXT    NOT NULL DEFAULT '',
            subject          TEXT    NOT NULL DEFAULT '',
            total_size_bytes INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE attachments (
            sha256            TEXT    NOT NULL,
            size              INTEGER NOT NULL,
            mime              TEXT    NOT NULL,
            original_filename TEXT    NOT NULL,
            stored_path       TEXT    NOT NULL,
            email_stable_id   TEXT    NOT NULL,
            PRIMARY KEY (stored_path, email_stable_id)
        )
    """)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def _column_names(db_path: pathlib.Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


def _user_version(db_path: pathlib.Path) -> int:
    conn = sqlite3.connect(str(db_path))
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    return v


# ── new DB tests ──────────────────────────────────────────────────────────────

def test_new_db_has_v2_columns(tmp_path):
    """A brand-new DB is created with all v2 columns."""
    db = tmp_path / "new.sqlite"
    conn = _init_db(db)
    conn.close()
    cols = _column_names(db, "emails")
    assert "to_addrs" in cols
    assert "cc_addrs" in cols
    assert "body_text" in cols


def test_new_db_has_correct_user_version(tmp_path):
    db = tmp_path / "new.sqlite"
    conn = _init_db(db)
    conn.close()
    assert _user_version(db) == 2


# ── migration tests ───────────────────────────────────────────────────────────

def test_v1_db_gets_new_columns(tmp_path):
    """An existing v1 DB gains the three new columns after _init_db."""
    db = tmp_path / "v1.sqlite"
    _make_v1_db(db)
    conn = _init_db(db)
    conn.close()
    cols = _column_names(db, "emails")
    assert "to_addrs" in cols
    assert "cc_addrs" in cols
    assert "body_text" in cols


def test_v1_db_user_version_becomes_2(tmp_path):
    db = tmp_path / "v1.sqlite"
    _make_v1_db(db)
    conn = _init_db(db)
    conn.close()
    assert _user_version(db) == 2


def test_v1_existing_data_preserved(tmp_path):
    """Existing rows survive the in-place migration."""
    db = tmp_path / "v1data.sqlite"
    _make_v1_db(db)
    raw = sqlite3.connect(str(db))
    raw.execute("""
        INSERT INTO emails (mailbox, stable_id, filepath, folder, date, from_addr, subject, total_size_bytes)
        VALUES ('mb', 'aabbcc', '/path/to/file.eml', 'INBOX', '2024-01-01', 'a@x.com', 'hi', 1234)
    """)
    raw.commit()
    raw.close()

    conn = _init_db(db)
    row = conn.execute("SELECT * FROM emails WHERE stable_id='aabbcc'").fetchone()
    conn.close()

    assert row is not None
    assert row["mailbox"] == "mb"
    assert row["to_addrs"] == ""   # new column default value
    assert row["cc_addrs"] == ""
    assert row["body_text"] == ""


def test_init_db_idempotent_on_v2(tmp_path):
    """Calling _init_db twice on a v2 DB does not raise."""
    db = tmp_path / "v2.sqlite"
    c1 = _init_db(db)
    c1.close()
    c2 = _init_db(db)  # should not raise
    c2.close()
    assert _user_version(db) == 2


def test_init_db_idempotent_on_migrated_v1(tmp_path):
    """Calling _init_db twice on a migrated-from-v1 DB does not raise."""
    db = tmp_path / "v1m.sqlite"
    _make_v1_db(db)
    c1 = _init_db(db)
    c1.close()
    c2 = _init_db(db)
    c2.close()
    assert _user_version(db) == 2
```

- [ ] **Step 2: Run tests — confirm they fail (red)**

```bash
PYTHONPATH=src python -m pytest tests/test_index_schema_migration_v2.py -v 2>&1 | tail -20
```

Expected: tests fail because `user_version` is never set and new columns don't exist.

---

## Task 6: Upgrade index_mailbox.py — New Columns + Migration + WAL

**Files:** Modify `src/maildir_report/index_mailbox.py`

- [ ] **Step 1: Add `_SCHEMA_VERSION` constant**

At the top of the module, after the imports, add:

```python
_SCHEMA_VERSION = 2
```

- [ ] **Step 2: Update `_CREATE_EMAILS` DDL to include new columns**

Replace the existing `_CREATE_EMAILS` string:

```python
_CREATE_EMAILS = """
CREATE TABLE IF NOT EXISTS emails (
    mailbox           TEXT    NOT NULL,
    stable_id         TEXT    NOT NULL PRIMARY KEY,
    filepath          TEXT    NOT NULL,
    folder            TEXT    NOT NULL DEFAULT '',
    date              TEXT    NOT NULL DEFAULT '',
    from_addr         TEXT    NOT NULL DEFAULT '',
    subject           TEXT    NOT NULL DEFAULT '',
    total_size_bytes  INTEGER NOT NULL DEFAULT 0,
    to_addrs          TEXT    NOT NULL DEFAULT '',
    cc_addrs          TEXT    NOT NULL DEFAULT '',
    body_text         TEXT    NOT NULL DEFAULT ''
);
"""
```

- [ ] **Step 3: Replace `_init_db` to handle schema migration**

Replace the entire `_init_db` function with:

```python
def _init_db(db_path: pathlib.Path) -> sqlite3.Connection:
    """Open (or create) a SQLite database, ensure schema exists, migrate if needed."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    # Create tables (IF NOT EXISTS — safe for both new and existing DBs)
    conn.execute(_CREATE_EMAILS)
    conn.execute(_CREATE_ATTACHMENTS)
    for idx_sql in _CREATE_INDEXES:
        conn.execute(idx_sql)
    conn.commit()

    # Version-based migration
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < _SCHEMA_VERSION:
        if version == 1:
            # v1 → v2: add three new columns to emails
            for col_def in [
                "to_addrs TEXT NOT NULL DEFAULT ''",
                "cc_addrs TEXT NOT NULL DEFAULT ''",
                "body_text TEXT NOT NULL DEFAULT ''",
            ]:
                try:
                    conn.execute(f"ALTER TABLE emails ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass  # column already exists — idempotent
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()

    return conn
```

- [ ] **Step 4: Update `_upsert_email` to write new columns**

Replace the existing `_upsert_email` function with:

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
            (mailbox, stable_id, filepath, folder, date, from_addr, subject,
             total_size_bytes, to_addrs, cc_addrs, body_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mailbox,
            email_rec["stable_id"],
            email_rec.get("filepath", ""),
            email_rec.get("folder", ""),
            email_rec.get("date", ""),
            email_rec.get("sender", ""),
            email_rec.get("subject", ""),
            email_rec.get("total_size", 0),
            email_rec.get("to", ""),       # maps parser's "to" → to_addrs column
            email_rec.get("cc_addrs", ""),
            email_rec.get("body_text", ""),
        ),
    )
```

- [ ] **Step 5: Add WAL checkpoint after bulk indexing in `index_mailbox()`**

In the `index_mailbox()` function, find the `conn.commit()` call that comes right after the `for email_rec in emails:` loop ends (before the `finally:` block). Add the checkpoint calls immediately after:

```python
        conn.commit()
        if global_conn:
            global_conn.commit()

        # Checkpoint WAL to prevent large lingering WAL files
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
        if global_conn:
            global_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            global_conn.commit()
```

- [ ] **Step 6: Run schema migration tests — confirm they pass (green)**

```bash
PYTHONPATH=src python -m pytest tests/test_index_schema_migration_v2.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 7: Run full test suite — no regressions**

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/maildir_report/index_mailbox.py \
        tests/test_index_schema_migration_v2.py
git commit -m "feat(index): add to/cc/body_text columns with v1→v2 migration and WAL checkpoint"
```

---

## Task 7: MySQL Archive Schema Migration

**Files:** Create `web/migrations/001_archive_schema.sql`

- [ ] **Step 1: Create the migration file**

```sql
-- web/migrations/001_archive_schema.sql
-- Archive tables for the mailbox search database.
-- Replaces the old review workflow tables.

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
  KEY idx_archive_emails_date (mailbox, date),
  FULLTEXT KEY ftx_archive_emails (subject, from_addr, to_addrs, cc_addrs, body_text)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS archive_attachments (
  mailbox           VARCHAR(255) NOT NULL,
  email_stable_id   CHAR(64)     NOT NULL,
  sha256            CHAR(64)     NOT NULL,
  size              BIGINT       NOT NULL DEFAULT 0,
  mime              VARCHAR(255) NOT NULL DEFAULT '',
  original_filename TEXT         NOT NULL DEFAULT '',
  stored_path       TEXT         NOT NULL DEFAULT '',
  imported_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (mailbox, email_stable_id, sha256),
  KEY idx_archive_attachments_email (mailbox, email_stable_id),
  KEY idx_archive_attachments_sha256 (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: Verify migration applies cleanly**

```bash
devenv shell -- db-migrate
```

Expected output contains:
```
  [apply] 001_archive_schema.sql
==> Migrations complete. Applied: 1
```

- [ ] **Step 3: Commit**

```bash
git add web/migrations/001_archive_schema.sql
git commit -m "feat(db): add MySQL archive tables with FULLTEXT index"
```

---

## Task 8: Create import_archive.php

**Files:** Create `web/src/cli/import_archive.php`

- [ ] **Step 1: Create the importer**

```php
<?php
/**
 * web/src/cli/import_archive.php — Import global SQLite index into MySQL.
 *
 * Reads all rows from the global mail_index.sqlite and upserts them into
 * archive_emails and archive_attachments in MySQL.
 *
 * Idempotent: re-running is safe (INSERT ... ON DUPLICATE KEY UPDATE).
 * Chunked commits every 5000 rows.
 *
 * Usage: php import_archive.php [--sqlite <path>] [--config <path>] [--help]
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "CLI only.\n");
    exit(1);
}

$opts = getopt('', ['sqlite:', 'config:', 'help', 'h']);

if (isset($opts['help']) || isset($opts['h'])) {
    fwrite(STDOUT, <<<USAGE
    Usage: php import_archive.php [OPTIONS]

      Import the global SQLite mail index into MySQL archive tables.
      Idempotent — safe to re-run.

    Options:
      --sqlite <path>   Path to global SQLite index
                        (default: <data_dir>/index/mail_index.sqlite)
      --config <path>   Path to local.php config
                        (default: web/config/local.php)
      --help            Show this message

    Exit codes:
      0  Success
      1  Error

    USAGE);
    exit(0);
}

$scriptDir  = dirname(__DIR__, 2); // web/
$configPath = $opts['config'] ?? ($scriptDir . '/config/local.php');

if (!is_file($configPath)) {
    fwrite(STDERR, "ERROR: Config not found: $configPath\n");
    fwrite(STDERR, "  Copy web/config/local.php.example -> web/config/local.php\n");
    exit(1);
}

/** @var array<string,mixed> $config */
$config  = require $configPath;
$dbCfg   = $config['db']       ?? [];
$dataDir = rtrim($config['data_dir'] ?? '', '/');

$sqlitePath = $opts['sqlite'] ?? ($dataDir . '/index/mail_index.sqlite');

if (!file_exists($sqlitePath)) {
    fwrite(STDERR, "ERROR: SQLite index not found: $sqlitePath\n");
    fwrite(STDERR, "  Run: sync-all (or index-mailbox) first.\n");
    exit(1);
}

// ── MySQL connection ───────────────────────────────────────────────────────
$socket = $dbCfg['socket'] ?? '';
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

// ── SQLite (read-only) ─────────────────────────────────────────────────────
$sqlite = new SQLite3($sqlitePath, SQLITE3_OPEN_READONLY);
$sqlite->busyTimeout(5000);

// ── Import emails ──────────────────────────────────────────────────────────
$stmtEmail = $pdo->prepare(<<<SQL
    INSERT INTO archive_emails
        (mailbox, stable_id, filepath, folder, date, from_addr,
         to_addrs, cc_addrs, subject, body_text, total_size_bytes)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
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

$chunk       = 0;
$totalEmails = 0;
$pdo->beginTransaction();

$res = $sqlite->query(
    "SELECT mailbox, stable_id, filepath, folder, date, from_addr,
            to_addrs, cc_addrs, subject, body_text, total_size_bytes
     FROM emails"
);
while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
    $stmtEmail->execute([
        $row['mailbox'],
        $row['stable_id'],
        $row['filepath'],
        $row['folder'],
        $row['date'],
        $row['from_addr'],
        $row['to_addrs']   ?? '',
        $row['cc_addrs']   ?? '',
        $row['subject'],
        $row['body_text']  ?? '',
        (int) $row['total_size_bytes'],
    ]);
    $chunk++;
    $totalEmails++;
    if ($chunk >= 5000) {
        $pdo->commit();
        $pdo->beginTransaction();
        $chunk = 0;
        fwrite(STDOUT, "  ... $totalEmails emails\n");
    }
}
$pdo->commit();
fwrite(STDOUT, "Emails imported: $totalEmails\n");

// ── Import attachments (JOIN emails to resolve mailbox) ────────────────────
$stmtAtt = $pdo->prepare(<<<SQL
    INSERT INTO archive_attachments
        (mailbox, email_stable_id, sha256, size, mime, original_filename, stored_path)
    VALUES (?,?,?,?,?,?,?)
    ON DUPLICATE KEY UPDATE
        stored_path       = VALUES(stored_path),
        size              = VALUES(size),
        mime              = VALUES(mime),
        original_filename = VALUES(original_filename)
SQL);

$chunk    = 0;
$totalAtt = 0;
$pdo->beginTransaction();

$res2 = $sqlite->query(<<<SQL
    SELECT e.mailbox,
           a.email_stable_id,
           a.sha256,
           a.size,
           a.mime,
           a.original_filename,
           a.stored_path
    FROM attachments a
    JOIN emails e ON e.stable_id = a.email_stable_id
SQL);
while ($row = $res2->fetchArray(SQLITE3_ASSOC)) {
    $stmtAtt->execute([
        $row['mailbox'],
        $row['email_stable_id'],
        $row['sha256'],
        (int) $row['size'],
        $row['mime'],
        $row['original_filename'],
        $row['stored_path'],
    ]);
    $chunk++;
    $totalAtt++;
    if ($chunk >= 5000) {
        $pdo->commit();
        $pdo->beginTransaction();
        $chunk = 0;
        fwrite(STDOUT, "  ... $totalAtt attachments\n");
    }
}
$pdo->commit();
fwrite(STDOUT, "Attachments imported: $totalAtt\n");

$sqlite->close();
fwrite(STDOUT, "==> Import complete.\n");
exit(0);
```

- [ ] **Step 2: Verify syntax**

```bash
php -l web/src/cli/import_archive.php
```

Expected: `No syntax errors detected in web/src/cli/import_archive.php`

- [ ] **Step 3: Verify --help works**

```bash
devenv shell -- php web/src/cli/import_archive.php --help
```

Expected: usage text printed, exit 0.

- [ ] **Step 4: Commit**

```bash
git add web/src/cli/import_archive.php
git commit -m "feat(cli): add import_archive.php — SQLite to MySQL importer"
```

---

## Task 9: Create search_archive.php

**Files:** Create `web/src/cli/search_archive.php`

- [ ] **Step 1: Create the search CLI**

```php
<?php
/**
 * web/src/cli/search_archive.php — Search the mail archive via MySQL FULLTEXT.
 *
 * Usage: php search_archive.php --query <text> [--mailbox <name>] [--limit <n>]
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "CLI only.\n");
    exit(1);
}

$opts = getopt('', ['query:', 'mailbox:', 'limit:', 'config:', 'help', 'h']);

if (isset($opts['help']) || isset($opts['h']) || !isset($opts['query'])) {
    fwrite(STDOUT, <<<USAGE
    Usage: php search_archive.php --query <text> [OPTIONS]

      Search all archived emails using MySQL FULLTEXT (subject, from, to, cc, body).

    Options:
      --query <text>      Search terms (required). Supports MySQL boolean operators.
      --mailbox <name>    Restrict to one mailbox (optional).
      --limit <n>         Maximum results to show (default: 50).
      --config <path>     Path to local.php (default: web/config/local.php).
      --help              Show this message.

    Examples:
      php search_archive.php --query "invoice"
      php search_archive.php --query "invoice" --mailbox gabriel.hangel --limit 20

    USAGE);
    exit(isset($opts['query']) ? 1 : 0);
}

$scriptDir  = dirname(__DIR__, 2);
$configPath = $opts['config'] ?? ($scriptDir . '/config/local.php');

if (!is_file($configPath)) {
    fwrite(STDERR, "ERROR: Config not found: $configPath\n");
    exit(1);
}

/** @var array<string,mixed> $config */
$config   = require $configPath;
$dbCfg    = $config['db'] ?? [];
$query    = (string) $opts['query'];
$mailbox  = isset($opts['mailbox']) ? (string) $opts['mailbox'] : null;
$limit    = max(1, (int) ($opts['limit'] ?? 50));

// ── MySQL connection ───────────────────────────────────────────────────────
$socket = $dbCfg['socket'] ?? '';
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
    ]);
} catch (PDOException $e) {
    fwrite(STDERR, "ERROR: DB connection failed: " . $e->getMessage() . "\n");
    exit(1);
}

// ── Build and execute query ────────────────────────────────────────────────
$sql = <<<SQL
    SELECT
        e.mailbox,
        e.stable_id,
        e.date,
        e.from_addr,
        e.to_addrs,
        e.subject,
        LEFT(e.body_text, 200)   AS body_preview,
        GROUP_CONCAT(
            a.original_filename
            ORDER BY a.original_filename
            SEPARATOR '; '
        )                        AS attachments
    FROM archive_emails e
    LEFT JOIN archive_attachments a
           ON a.mailbox = e.mailbox
          AND a.email_stable_id = e.stable_id
    WHERE MATCH(e.subject, e.from_addr, e.to_addrs, e.cc_addrs, e.body_text)
          AGAINST (? IN BOOLEAN MODE)
SQL;

$params = [$query];
if ($mailbox !== null) {
    $sql    .= " AND e.mailbox = ?";
    $params[] = $mailbox;
}
$sql .= " GROUP BY e.mailbox, e.stable_id ORDER BY e.date DESC LIMIT ?";
$params[] = $limit;

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$rows = $stmt->fetchAll();

// ── Print results ──────────────────────────────────────────────────────────
if (!$rows) {
    fwrite(STDOUT, "No results for: $query\n");
    exit(0);
}

foreach ($rows as $row) {
    $preview = trim(preg_replace('/\s+/', ' ', $row['body_preview'] ?? ''));
    fwrite(STDOUT, "[{$row['mailbox']}] {$row['date']}  From: {$row['from_addr']}\n");
    fwrite(STDOUT, "  Subject: {$row['subject']}\n");
    if ($row['to_addrs']) {
        fwrite(STDOUT, "  To: {$row['to_addrs']}\n");
    }
    if ($row['attachments']) {
        fwrite(STDOUT, "  Attachments: {$row['attachments']}\n");
    }
    if ($preview) {
        fwrite(STDOUT, "  Preview: " . mb_substr($preview, 0, 120) . "\n");
    }
    fwrite(STDOUT, "\n");
}

fwrite(STDOUT, count($rows) . " result(s) for: $query\n");
exit(0);
```

- [ ] **Step 2: Verify syntax**

```bash
php -l web/src/cli/search_archive.php
```

Expected: `No syntax errors detected`

- [ ] **Step 3: Commit**

```bash
git add web/src/cli/search_archive.php
git commit -m "feat(cli): add search_archive.php — MySQL FULLTEXT search CLI"
```

---

## Task 10: Update devenv.nix

**Files:** Modify `devenv.nix`

- [ ] **Step 1: Replace the full devenv.nix**

Write the new `devenv.nix` with old scripts removed and new scripts added. The file should contain:

```nix
{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
{
  # ── Python pipeline ───────────────────────────────────────────────────────
  languages.python = {
    enable = true;
    venv = {
      enable = true;
      requirements = ''
        pytest>=8.0
      '';
    };
  };

  # ── PHP 8.3 (frameworkless) ───────────────────────────────────────────────
  languages.php = {
    enable = true;
    package = pkgs.php83;
  };

  # ── MariaDB (MySQL-compatible) for local dev ──────────────────────────────
  services.mysql = {
    enable = true;
    package = pkgs.mariadb;
    initialDatabases = [{ name = "mailreview"; }];
    ensureUsers = [
      {
        name = "mailreview";
        ensurePermissions = {
          "mailreview.*" = "ALL PRIVILEGES";
        };
      }
    ];
  };

  # ── Extra CLI tools ───────────────────────────────────────────────────────
  packages = with pkgs; [
    jq
    curl
    rsync
  ];

  # ── devenv scripts ────────────────────────────────────────────────────────
  scripts = {

    # ── db-start: start the local MariaDB dev server ──────────────────────
    db-start.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: db-start"
        echo "  Start the local MariaDB dev server managed by devenv."
        exit 0
      fi
      echo "==> Starting MariaDB via devenv process manager..."
      devenv up &
      echo "==> Waiting for MariaDB socket..."
      for i in $(seq 1 30); do
        mysql -u mailreview --socket="$DEVENV_STATE/mysql.sock" \
          -e "SELECT 1" mailreview >/dev/null 2>&1 && break
        sleep 1
      done
      mysql -u mailreview --socket="$DEVENV_STATE/mysql.sock" \
        -e "SELECT VERSION();" mailreview \
        || { echo "ERROR: MariaDB not responding after 30s"; exit 1; }
      echo "==> MariaDB ready."
    '';

    # ── db-migrate: run SQL migrations ────────────────────────────────────
    db-migrate.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: db-migrate [--socket <path>]"
        echo "  Run pending SQL migrations against MariaDB."
        exit 0
      fi
      SOCK="''${DB_SOCKET:-$DEVENV_STATE/mysql.sock}"
      if [ "''${1:-}" = "--socket" ] && [ -n "''${2:-}" ]; then
        SOCK="$2"
      fi
      php "$DEVENV_ROOT/web/src/cli/migrate.php" --socket "$SOCK"
    '';

    # ── extract-attachments: extract MIME attachments for one mailbox ──────
    extract-attachments.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: extract-attachments <mailbox>"
        echo "  Extract MIME attachments from the stored Maildir to attachments/."
        exit 0
      fi
      if [ -z "''${1:-}" ]; then
        echo "ERROR: mailbox name required"
        echo "Run: extract-attachments --help"
        exit 1
      fi
      MAILBOX="$1"
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      echo "==> [extract-attachments] $MAILBOX"
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
        --maildir-root "$DATA_ROOT/maildir/.maildir" \
        --output-root  "$DATA_ROOT/attachments" \
        || { echo "ERROR: extraction failed for $MAILBOX"; exit 1; }
      echo "==> Done."
    '';

    # ── index-mailbox: (re)build per-mailbox SQLite index ─────────────────
    index-mailbox.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: index-mailbox <mailbox> [--global-index <path>]"
        echo "  Build the SQLite index for a stored mailbox."
        exit 0
      fi
      if [ -z "''${1:-}" ]; then
        echo "ERROR: mailbox name required"; exit 1
      fi
      MAILBOX="$1"
      shift
      GLOBAL_ARG=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --global-index) GLOBAL_ARG="--global-index $2"; shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      echo "==> [index-mailbox] $MAILBOX"
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
        --mailbox "$MAILBOX" \
        --data-root "$DATA_ROOT" \
        $GLOBAL_ARG \
        || { echo "ERROR: indexing failed for $MAILBOX"; exit 1; }
      echo "==> Done."
    '';

    # ── index-all: (re)build global index across all mailboxes ────────────
    index-all.exec = ''
      GLOBAL_INDEX="$DEVENV_ROOT/data/index/mail_index.sqlite"
      mkdir -p "$DEVENV_ROOT/data/index"
      echo "==> [index-all] Building global index..."
      for MAILBOX_DIR in "$DEVENV_ROOT/data/mailboxes"/*/; do
        MAILBOX="$(basename "$MAILBOX_DIR")"
        echo "    indexing: $MAILBOX"
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
          --mailbox "$MAILBOX" \
          --data-root "$MAILBOX_DIR" \
          --global-index "$GLOBAL_INDEX" 2>/dev/null \
          || echo "    WARNING: index failed for $MAILBOX (skipping)"
      done
      echo "==> Done. Global index: $GLOBAL_INDEX"
    '';

    # ── sync-all: full pipeline — rsync + extract + index + MySQL import ──
    sync-all.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: sync-all [OPTIONS]"
        echo ""
        echo "  Download all mailboxes, extract attachments, build SQLite indexes,"
        echo "  then import into MySQL archive tables."
        echo ""
        echo "  READ-ONLY: no changes are made to the remote server."
        echo ""
        echo "Options:"
        echo "  --mailboxes-file <path>   Use a local mailbox list instead of fetching"
        echo "                            from the server (useful for testing)."
        echo "  --src-base <base>         Override rsync source base URL."
        echo "                            Default: mrija_org@s16.thehost.com.ua:email/mrija.org"
        echo "                            Use a local path for fixture testing."
        echo "  --skip-import             Skip the MySQL import step."
        echo "  --mailbox <name>          Sync a single mailbox only."
        echo "  --help                    Show this message."
        exit 0
      fi

      MAILBOXES_FILE=""
      SRC_BASE="mrija_org@s16.thehost.com.ua:email/mrija.org"
      SKIP_IMPORT=0
      SINGLE_MAILBOX=""

      while [ $# -gt 0 ]; do
        case "$1" in
          --mailboxes-file) MAILBOXES_FILE="$2"; shift 2 ;;
          --src-base)       SRC_BASE="$2";       shift 2 ;;
          --skip-import)    SKIP_IMPORT=1;        shift   ;;
          --mailbox)        SINGLE_MAILBOX="$2";  shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      # Fetch mailbox list from server if not provided locally
      if [ -z "$MAILBOXES_FILE" ]; then
        MAILBOXES_FILE="$DEVENV_ROOT/data/mailboxes.txt"
        mkdir -p "$DEVENV_ROOT/data"
        echo "==> [sync-all] Fetching mailbox list from server..."
        rsync -az "$SRC_BASE/mailboxes.txt" "$MAILBOXES_FILE" \
          || { echo "ERROR: Could not fetch mailboxes.txt from $SRC_BASE"; exit 1; }
      fi

      if [ ! -f "$MAILBOXES_FILE" ]; then
        echo "ERROR: mailboxes file not found: $MAILBOXES_FILE"; exit 1
      fi

      # Parse and validate mailbox names
      if [ -n "$SINGLE_MAILBOX" ]; then
        MAILBOXES="$SINGLE_MAILBOX"
      else
        MAILBOXES=$(grep -v '^\s*#' "$MAILBOXES_FILE" \
                    | grep -v '^\s*$' \
                    | grep -E '^[A-Za-z0-9._-]+$' || true)
      fi

      if [ -z "$MAILBOXES" ]; then
        echo "ERROR: No valid mailbox names found in $MAILBOXES_FILE"; exit 1
      fi

      FAILED=""
      GLOBAL_INDEX="$DEVENV_ROOT/data/index/mail_index.sqlite"
      mkdir -p "$DEVENV_ROOT/data/index"

      for MAILBOX in $MAILBOXES; do
        echo ""
        echo "==> [sync-all] [$MAILBOX] Starting..."
        DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
        MAILDIR_DST="$DATA_ROOT/maildir/.maildir"
        ATTACHMENTS_DST="$DATA_ROOT/attachments"
        mkdir -p "$MAILDIR_DST" "$ATTACHMENTS_DST"

        # Step 1: rsync (read-only)
        echo "  [1/3] rsync $SRC_BASE/$MAILBOX/.maildir/ → $MAILDIR_DST/"
        rsync -az --info=progress2 \
          "$SRC_BASE/$MAILBOX/.maildir/" \
          "$MAILDIR_DST/" \
          || { echo "  ERROR: rsync failed for $MAILBOX"; FAILED="$FAILED $MAILBOX"; continue; }

        # Step 2: extract attachments
        echo "  [2/3] extracting attachments..."
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
          --maildir-root "$MAILDIR_DST" \
          --output-root  "$ATTACHMENTS_DST" \
          || { echo "  ERROR: extraction failed for $MAILBOX"; FAILED="$FAILED $MAILBOX"; continue; }

        # Step 3: index (per-mailbox + global)
        echo "  [3/3] indexing..."
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
          --mailbox "$MAILBOX" \
          --data-root "$DATA_ROOT" \
          --global-index "$GLOBAL_INDEX" \
          || { echo "  ERROR: indexing failed for $MAILBOX"; FAILED="$FAILED $MAILBOX"; continue; }

        echo "  Done: $MAILBOX"
      done

      # Step 4: MySQL import
      if [ "$SKIP_IMPORT" = "0" ]; then
        echo ""
        echo "==> [sync-all] Importing to MySQL..."
        php "$DEVENV_ROOT/web/src/cli/import_archive.php" \
          --sqlite "$GLOBAL_INDEX" \
          || { echo "ERROR: MySQL import failed"; FAILED="$FAILED MYSQL_IMPORT"; }
      fi

      echo ""
      if [ -n "$FAILED" ]; then
        echo "==> [sync-all] COMPLETED WITH FAILURES:$FAILED"
        exit 1
      fi
      echo "==> [sync-all] All done."
    '';

    # ── search-archive: search the MySQL archive ──────────────────────────
    search-archive.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: search-archive <query> [--mailbox <name>] [--limit <n>]"
        echo "  Search archived emails via MySQL FULLTEXT."
        exit 0
      fi
      if [ -z "''${1:-}" ]; then
        echo "ERROR: search query required"
        echo "Run: search-archive --help"
        exit 1
      fi
      QUERY="$1"
      shift
      MAILBOX_ARG=""
      LIMIT_ARG=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox) MAILBOX_ARG="--mailbox $2"; shift 2 ;;
          --limit)   LIMIT_ARG="--limit $2";    shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done
      php "$DEVENV_ROOT/web/src/cli/search_archive.php" \
        --query "$QUERY" \
        $MAILBOX_ARG \
        $LIMIT_ARG
    '';

  };

  # ── Shell welcome message ─────────────────────────────────────────────────
  enterShell = ''
    echo ""
    echo "  mailbox-archive devenv"
    echo "  ────────────────────────────────────────────────────────────"
    echo "  sync-all [--mailboxes-file f] [--src-base b] [--skip-import]"
    echo "           download all mailboxes + index + MySQL import"
    echo "  extract-attachments <mailbox>   extract MIME attachments"
    echo "  index-mailbox <mailbox>         rebuild SQLite index"
    echo "  index-all                       rebuild global SQLite index"
    echo "  db-start                        start local MariaDB"
    echo "  db-migrate                      run SQL migrations"
    echo "  search-archive <query>          search the archive"
    echo "  ────────────────────────────────────────────────────────────"
    echo "  data    : $DEVENV_ROOT/data/"
    echo ""
  '';
}
```

- [ ] **Step 2: Verify devenv evaluates without errors**

```bash
devenv shell -- echo "devenv ok"
```

Expected: `devenv ok` printed (may take a moment to rebuild).

- [ ] **Step 3: Verify new script help texts work**

```bash
devenv shell -- sync-all --help
devenv shell -- extract-attachments --help
devenv shell -- search-archive --help
```

Expected: each prints usage text and exits 0.

- [ ] **Step 4: Commit**

```bash
git add devenv.nix
git commit -m "feat(devenv): add sync-all, extract-attachments, search-archive; remove old scripts"
```

---

## Task 11: Create Test Fixtures

**Files:** Create fixture emails and mailbox list under `tests/fixtures/`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p tests/fixtures/src/qa_test_mailbox/.maildir/cur
mkdir -p tests/fixtures/src/qa_test_mailbox/.maildir/new
mkdir -p tests/fixtures/src/qa_test_mailbox/.maildir/tmp
```

- [ ] **Step 2: Create `tests/fixtures/mailboxes.txt`**

```
# Test mailbox list for QA — local fixtures only
qa_test_mailbox
```

- [ ] **Step 3: Create fixture email 001.eml (plain text with unique search token)**

Create `tests/fixtures/src/qa_test_mailbox/.maildir/cur/001.eml`:

```
From: alice@mrija.org
To: bob@mrija.org
Cc: carol@mrija.org
Subject: Archive test email one
Message-ID: <fixture-001@qa.test>
Date: Wed, 15 Jan 2025 09:00:00 +0000
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit
MIME-Version: 1.0

This is the first fixture email for QA testing.
It contains the search token: fixture_unique_keyword_alpha
And some other content for verification.
```

- [ ] **Step 4: Create fixture email 002.eml (with attachment)**

Create `tests/fixtures/src/qa_test_mailbox/.maildir/cur/002.eml`:

```
From: bob@mrija.org
To: alice@mrija.org
Subject: Archive test email two with attachment
Message-ID: <fixture-002@qa.test>
Date: Thu, 16 Jan 2025 10:30:00 +0000
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary_002"

--boundary_002
Content-Type: text/plain; charset=utf-8

Second fixture email. Contains: fixture_unique_keyword_beta
--boundary_002
Content-Type: application/pdf; name="test_document.pdf"
Content-Disposition: attachment; filename="test_document.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQKdGVzdA==
--boundary_002--
```

- [ ] **Step 5: Verify parser can read both fixture emails**

```bash
PYTHONPATH=src python3 -c "
from maildir_report.parser import parse_email_file
r1 = parse_email_file('tests/fixtures/src/qa_test_mailbox/.maildir/cur/001.eml', 'INBOX')
r2 = parse_email_file('tests/fixtures/src/qa_test_mailbox/.maildir/cur/002.eml', 'INBOX')
print('001 body_text:', repr(r1['body_text'][:50]))
print('001 cc_addrs:', repr(r1['cc_addrs']))
print('002 parts count:', len(r2['parts']))
"
```

Expected output:
```
001 body_text: 'This is the first fixture email for QA testing.\n'
001 cc_addrs: 'carol@mrija.org'
002 parts count: 1
```

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/
git commit -m "test(fixtures): add qa_test_mailbox fixture emails for integration QA"
```

---

## Task 12: Create qa-archive.sh

**Files:** Create `web/scripts/qa-archive.sh`

- [ ] **Step 1: Create the QA script**

```bash
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
SOCKET="${DEVENV_STATE:-}/mysql.sock"
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
devenv run db-start 2>/dev/null || true
devenv run db-migrate

echo ""
echo "==> [qa-archive] Step 2: sync fixture mailbox (no server)..."
devenv run sync-all \
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
RESULT=$(devenv run search-archive "fixture_unique_keyword_alpha" 2>&1 || true)
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
```

- [ ] **Step 2: Make executable and verify syntax**

```bash
chmod +x web/scripts/qa-archive.sh
bash -n web/scripts/qa-archive.sh
```

Expected: exits 0 (no syntax errors).

- [ ] **Step 3: Commit**

```bash
git add web/scripts/qa-archive.sh tests/fixtures/mailboxes.txt
git commit -m "test(qa): add qa-archive.sh end-to-end QA script"
```

---

## Task 13: Run Full Test Suite and QA

- [ ] **Step 1: Run pytest — confirm all tests pass**

```bash
PYTHONPATH=src python -m pytest tests/ -v
```

Expected: all tests `PASSED`, exit 0. Note the final count for reference.

- [ ] **Step 2: Run the end-to-end QA script**

First ensure MariaDB is running:

```bash
devenv shell -- db-start
```

Then run QA:

```bash
devenv shell -- bash web/scripts/qa-archive.sh
```

Expected: `==> [qa-archive] ALL STEPS PASSED ✓` and exit 0.

- [ ] **Step 3: Test sync-all --help exits 0**

```bash
devenv shell -- sync-all --help
```

Expected: usage text, exit 0.

- [ ] **Step 4: Test search-archive with no results returns gracefully**

```bash
devenv shell -- search-archive "xyzzy_no_such_word_in_any_email"
```

Expected: `No results for: xyzzy_no_such_word_in_any_email` and exit 0.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: verify full test suite and QA pass after archive pipeline"
```

---

## Self-Review Checklist (complete before starting implementation)

**Spec coverage:**
- ✅ Download all mailboxes → `sync-all` (Task 10)
- ✅ Extract attachments → `extract_attachments.py` wrapper (Task 10)
- ✅ SQLite per-mailbox + global index → `index_mailbox.py` (Task 6)
- ✅ Parser CC + body_text → `parser.py` (Task 4)
- ✅ SQLite v1→v2 migration → Task 6
- ✅ WAL checkpoint → Task 6
- ✅ MySQL archive schema → Task 7
- ✅ MySQL importer → Task 8
- ✅ Search CLI → Task 9
- ✅ Testing every step with fixtures before server → Tasks 11–13
- ✅ Cleanup of old files → Task 1
- ✅ Read-only against server (rsync only) → enforced in `sync-all` (no delete commands)
- ✅ `--src-base` override for local fixture testing → `sync-all` flag

**Type consistency:**
- `parse_email_file` → returns `rec["cc_addrs"]` and `rec["body_text"]` (Task 4)
- `_upsert_email` → reads `email_rec.get("to", "")` for `to_addrs`, `email_rec.get("cc_addrs", "")`, `email_rec.get("body_text", "")` (Task 6)
- `import_archive.php` → reads SQLite columns `to_addrs`, `cc_addrs`, `body_text` (Task 8, matches Task 6 DDL)
- `search_archive.php` → queries `archive_emails.cc_addrs` (Task 9, matches Task 7 schema)

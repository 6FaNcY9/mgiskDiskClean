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

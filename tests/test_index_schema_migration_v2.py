# tests/test_index_schema_migration_v2.py
"""Tests for SQLite schema migration from v1 to v2 in index_mailbox.

v1 schema: emails has no to_addrs / cc_addrs / body_text columns, user_version=1.
v2 schema: adds those three columns, user_version=2.

These tests are RED before Task 6.
Run: pytest tests/test_index_schema_migration_v2.py -v
"""
import sqlite3
import pathlib

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

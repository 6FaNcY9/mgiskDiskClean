"""
src/web/db.py — SQLite database helpers.

This module opens the database, creates the schema if it doesn't exist yet,
and sets up SQLite FTS5 (full-text search) so we can search email bodies and
subjects quickly — without needing MySQL.

Key concepts used here:
  - WAL mode: "Write-Ahead Logging" lets readers and writers work at the same
    time without blocking each other.  Normally SQLite locks the whole file on
    writes, which would freeze the web UI.  WAL fixes this.
  - FTS5: SQLite's built-in full-text search engine.  It builds an inverted
    index (word → list of rows) so "MATCH 'invoice'" is fast even on 100k rows,
    whereas "LIKE '%invoice%'" has to scan every row.
  - Content table: the FTS5 virtual table doesn't duplicate the data; it just
    stores the index and tells SQLite to look up the actual text in archive_emails.

Usage (inside a FastAPI route):
    from src.web.db import get_db
    def my_route(db: sqlite3.Connection = Depends(get_db)):
        rows = db.execute("SELECT ...").fetchall()
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator


# The single global path to the database.
# This is set once in app.py when the server starts; routes read it via get_db().
_db_path: Path | None = None


def set_db_path(path: Path) -> None:
    """Tell this module which SQLite file to use.

    Called once at application startup (inside the lifespan function in app.py).
    """
    global _db_path
    _db_path = path


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *path* with good defaults.

    Returns a connection that yields rows as dicts instead of plain tuples,
    which makes template code like  row['subject']  possible.
    """
    db = sqlite3.connect(str(path), check_same_thread=False)

    # Row factory: makes db.execute("SELECT a,b").fetchone() return
    # {'a': ..., 'b': ...} instead of a plain tuple.
    db.row_factory = sqlite3.Row

    # WAL mode: see module docstring.  Must be set before any reads/writes.
    db.execute("PRAGMA journal_mode=WAL")

    # Enforce foreign-key constraints (SQLite ignores them by default).
    db.execute("PRAGMA foreign_keys=ON")

    # Small timeout so concurrent writes queue up instead of raising immediately.
    db.execute("PRAGMA busy_timeout=5000")

    return db


def init_db(path: Path) -> None:
    """Create tables and indexes that don't exist yet.

    This is idempotent: safe to call every time the server starts.
    It also handles the FTS5 migration for databases built by the old PHP script
    (build_client_sqlite.php) which did not create the FTS5 table.
    """
    db = open_db(path)

    # ── Core tables ───────────────────────────────────────────────────────────
    # These mirror the schema in build_client_sqlite.php so both the PHP and
    # Python paths produce the same structure.
    db.executescript("""
        CREATE TABLE IF NOT EXISTS archive_emails (
            mailbox          TEXT NOT NULL,
            stable_id        TEXT NOT NULL,
            filepath         TEXT NOT NULL,
            folder           TEXT NOT NULL DEFAULT '',
            date             TEXT NOT NULL DEFAULT '',
            from_addr        TEXT NOT NULL DEFAULT '',
            to_addrs         TEXT NOT NULL DEFAULT '',
            cc_addrs         TEXT NOT NULL DEFAULT '',
            subject          TEXT NOT NULL DEFAULT '',
            body_text        TEXT NOT NULL DEFAULT '',
            total_size_bytes INTEGER NOT NULL DEFAULT 0,
            imported_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, stable_id)
        );
        CREATE INDEX IF NOT EXISTS idx_archive_emails_mailbox_date
            ON archive_emails (mailbox, date);
        CREATE INDEX IF NOT EXISTS idx_archive_emails_date
            ON archive_emails (date);

        CREATE TABLE IF NOT EXISTS archive_attachments (
            mailbox           TEXT NOT NULL,
            email_stable_id   TEXT NOT NULL,
            stored_path       TEXT NOT NULL,
            sha256            TEXT NOT NULL,
            size              INTEGER NOT NULL DEFAULT 0,
            mime              TEXT NOT NULL DEFAULT '',
            original_filename TEXT NOT NULL DEFAULT '',
            imported_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id, sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_archive_attachments_email
            ON archive_attachments (mailbox, email_stable_id);
        CREATE INDEX IF NOT EXISTS idx_archive_attachments_sha256
            ON archive_attachments (sha256);

        CREATE TABLE IF NOT EXISTS vt_cache (
            sha256     TEXT PRIMARY KEY,
            status     TEXT NOT NULL,
            scan_id    TEXT NOT NULL DEFAULT '',
            positives  INTEGER NOT NULL DEFAULT 0,
            scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS review_decisions (
            mailbox          TEXT NOT NULL,
            email_stable_id  TEXT NOT NULL,
            decision         TEXT NOT NULL CHECK (decision IN ('keep', 'delete', 'unsure')),
            notes            TEXT NOT NULL DEFAULT '',
            reviewer_role    TEXT NOT NULL DEFAULT '',
            reviewer_name    TEXT NOT NULL DEFAULT '',
            decided_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id)
        );
        CREATE INDEX IF NOT EXISTS idx_review_decisions_decision
            ON review_decisions (decision);
        CREATE INDEX IF NOT EXISTS idx_review_decisions_decided_at
            ON review_decisions (decided_at);
    """)

    # ── FTS5 virtual table ────────────────────────────────────────────────────
    # This is a "content table" FTS index — the actual text is still in
    # archive_emails; FTS5 only stores the search index.
    # We check for existence first because CREATE VIRTUAL TABLE doesn't support
    # IF NOT EXISTS in older SQLite builds.
    fts_exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='emails_fts'"
    ).fetchone()

    if not fts_exists:
        # Create the FTS5 index.
        # stable_id and mailbox are UNINDEXED because we never search *for* them —
        # they're just carried along so we can join back to archive_emails.
        db.execute("""
            CREATE VIRTUAL TABLE emails_fts USING fts5(
                stable_id UNINDEXED,
                mailbox   UNINDEXED,
                subject,
                from_addr,
                to_addrs,
                cc_addrs,
                body_text,
                content=archive_emails,
                content_rowid=rowid
            )
        """)

        # Populate the index from existing rows.
        # This only runs once (when the table is first created).
        db.execute("""
            INSERT INTO emails_fts(rowid, stable_id, mailbox, subject,
                                   from_addr, to_addrs, cc_addrs, body_text)
            SELECT rowid, stable_id, mailbox, subject,
                   from_addr, to_addrs, cc_addrs, body_text
            FROM archive_emails
        """)

        # Trigger: keep the FTS index in sync when new emails are inserted.
        # Without this, emails imported after startup wouldn't be searchable.
        db.execute("""
            CREATE TRIGGER emails_fts_insert AFTER INSERT ON archive_emails BEGIN
                INSERT INTO emails_fts(rowid, stable_id, mailbox, subject,
                                       from_addr, to_addrs, cc_addrs, body_text)
                VALUES (new.rowid, new.stable_id, new.mailbox, new.subject,
                        new.from_addr, new.to_addrs, new.cc_addrs, new.body_text);
            END
        """)

        # Trigger: keep FTS in sync on deletes (needed for review/cleanup workflows).
        db.execute("""
            CREATE TRIGGER emails_fts_delete BEFORE DELETE ON archive_emails BEGIN
                INSERT INTO emails_fts(emails_fts, rowid, stable_id, mailbox, subject,
                                       from_addr, to_addrs, cc_addrs, body_text)
                VALUES ('delete', old.rowid, old.stable_id, old.mailbox, old.subject,
                        old.from_addr, old.to_addrs, old.cc_addrs, old.body_text);
            END
        """)

    db.commit()
    db.close()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency that yields an open database connection.

    Use it in route functions like this:
        from fastapi import Depends
        import sqlite3

        def my_route(db: sqlite3.Connection = Depends(get_db)):
            rows = db.execute("SELECT * FROM archive_emails LIMIT 10").fetchall()

    The connection is automatically closed when the request finishes.
    The 'yield' pattern (instead of 'return') is what makes FastAPI close it.
    """
    if _db_path is None:
        raise RuntimeError("Database path not set — call set_db_path() first")

    db = open_db(_db_path)
    try:
        yield db
    finally:
        db.close()

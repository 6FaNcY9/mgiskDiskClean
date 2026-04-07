"""
index_mailbox.py — Build a SQLite index for a stored mailbox.

Purpose
-------
Scan a stored Maildir and its extracted attachments directory, then persist
metadata into a SQLite database at::

    <data_root>/index.sqlite          (per-mailbox index)

An optional *global* index can also be populated at a caller-specified path::

    <global_index_path>               (e.g. $DEVENV_ROOT/data/index/mail_index.sqlite)

Schema
------
Two tables are created if they do not exist:

``emails``
    One row per unique email (keyed by ``stable_id``).

    Columns:
      mailbox           TEXT NOT NULL
      stable_id         TEXT NOT NULL PRIMARY KEY
      filepath          TEXT NOT NULL
      folder            TEXT NOT NULL
      date              TEXT NOT NULL
      from_addr         TEXT NOT NULL     -- "from" is a SQL reserved word
      subject           TEXT NOT NULL
      total_size_bytes  INTEGER NOT NULL

``attachments``
    ``attachments``
    One row per (email, stored_path) pair.  Multiple emails containing the
    same attachment payload each get their own row, pointing to the same
    ``stored_path`` on disk.  PK is (stored_path, email_stable_id).

    Columns:
      sha256            TEXT NOT NULL
      size              INTEGER NOT NULL
      mime              TEXT NOT NULL
      original_filename TEXT NOT NULL
      stored_path       TEXT NOT NULL               -- on-disk file (sha256_size.ext)
      email_stable_id   TEXT NOT NULL     -- FK to emails.stable_id
      PRIMARY KEY (stored_path, email_stable_id)
      email_stable_id   TEXT NOT NULL     -- FK to emails.stable_id

Idempotence
-----------
All inserts use ``INSERT OR REPLACE`` (upsert).  Running the indexer on the
same mailbox data multiple times produces identical row counts — no duplicates.

Attachment linking
------------------
An attachment row links to its email via ``email_stable_id``.  The
``stored_path`` is derived from the same ``<sha256>_<size>.<ext>`` naming used
by ``extract_attachments.py``, so the index matches the files on disk.

Note: when the same content (same sha256+size+ext) appears in multiple emails,
only the *first* email's ``stored_path`` file exists on disk (subsequent
extractions are skipped as duplicates).  Each email that contains that part
gets its own ``attachments`` row, but ``stored_path`` points to the same file.

Public API
----------
IndexResult
    Dataclass returned by ``index_mailbox()``.

index_mailbox(mailbox, data_root, global_index_path=None) -> IndexResult
    Main indexing function.

main(argv=None) -> int
    CLI entrypoint.  ``python -m maildir_report.index_mailbox --help``.
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any

from maildir_report.extract_attachments import (
    _is_extractable_part,
    _stored_filename,
)
from maildir_report.hash import sha256_hex
from maildir_report.parser import scan_maildir

_SCHEMA_VERSION = 2

# ── DDL ───────────────────────────────────────────────────────────────────────

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

_CREATE_ATTACHMENTS = """
CREATE TABLE IF NOT EXISTS attachments (
    sha256            TEXT    NOT NULL,
    size              INTEGER NOT NULL,
    mime              TEXT    NOT NULL,
    original_filename TEXT    NOT NULL,
    stored_path       TEXT    NOT NULL,
    email_stable_id   TEXT    NOT NULL,
    PRIMARY KEY (stored_path, email_stable_id)
);
"""

# Indexes for common search patterns.
_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_emails_mailbox ON emails (mailbox);",
    "CREATE INDEX IF NOT EXISTS idx_emails_date ON emails (date);",
    "CREATE INDEX IF NOT EXISTS idx_attachments_sha256 ON attachments (sha256);",
    "CREATE INDEX IF NOT EXISTS idx_attachments_email ON attachments (email_stable_id);",
    "CREATE INDEX IF NOT EXISTS idx_attachments_orig_filename ON attachments (original_filename);",
]


# ── result dataclass ──────────────────────────────────────────────────────────


@dataclass
class IndexResult:
    """Result of an indexing run.

    Attributes
    ----------
    emails_indexed : int
        Number of email rows written (INSERT OR REPLACE).
    attachments_indexed : int
        Number of attachment rows written (INSERT OR REPLACE).
    index_path : str
        Absolute path of the per-mailbox SQLite file.
    global_index_path : str | None
        Absolute path of the global SQLite file, or None when not used.
    """

    emails_indexed: int = 0
    attachments_indexed: int = 0
    index_path: str = ""
    global_index_path: str | None = None


# ── schema helpers ────────────────────────────────────────────────────────────


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
            email_rec.get("to", ""),
            email_rec.get("cc_addrs", ""),
            email_rec.get("body_text", ""),
        ),
    )


def _upsert_attachment(
    conn: sqlite3.Connection,
    part: dict[str, Any],
    email_stable_id: str,
    attachment_dir: pathlib.Path,
) -> None:
    """INSERT OR REPLACE one attachment row.

    The ``stored_path`` is the absolute path that ``extract_attachments``
    would write (whether or not the file actually exists yet on disk).
    """
    sha256 = part.get("content_hash", "")
    size = part.get("size", 0)
    payload: bytes | bytearray | None = part.get("payload_bytes")
    payload_bytes = payload if isinstance(payload, (bytes, bytearray)) else b""

    if not sha256:
        sha256 = sha256_hex(payload_bytes)

    original_filename = part.get("filename", "")
    mime = part.get("mime", "")

    stored_name = _stored_filename(sha256, size, original_filename)
    stored_path = str(attachment_dir / stored_name)

    conn.execute(
        """
        INSERT OR REPLACE INTO attachments
            (sha256, size, mime, original_filename, stored_path, email_stable_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            sha256,
            size,
            mime,
            original_filename,
            stored_path,
            email_stable_id,
        ),
    )


# ── indexing logic ────────────────────────────────────────────────────────────


def index_mailbox(
    mailbox: str,
    data_root: str,
    global_index_path: str | None = None,
) -> IndexResult:
    """Build (or rebuild) the SQLite index for one mailbox.

    Scans the Maildir under ``<data_root>/maildir/.maildir/`` and indexes all
    email metadata and attachment metadata.

    Parameters
    ----------
    mailbox:
        Mailbox name (e.g. ``"gabriel.hangel"``).  Stored in every email row.
    data_root:
        Path to the mailbox data directory.  Expected layout::

            <data_root>/maildir/.maildir/   — Maildir files
            <data_root>/attachments/        — extracted attachment files
            <data_root>/index.sqlite        — output index (created here)

    global_index_path:
        When provided, the same rows are also written to this SQLite file
        (created with the same schema if absent).

    Returns
    -------
    IndexResult
        Summary: row counts and index paths.

    Raises
    ------
    FileNotFoundError
        If the Maildir root path does not exist.
    """
    root = pathlib.Path(data_root)
    maildir_root = root / "maildir" / ".maildir"
    attachment_dir = root / "attachments"
    index_path = root / "index.sqlite"

    if not maildir_root.exists():
        raise FileNotFoundError(
            f"Maildir root not found: {maildir_root}\n"
            f"Run store-mailbox first to populate {maildir_root}."
        )

    # Ensure attachment dir exists (may not yet exist if extract hasn't run).
    attachment_dir.mkdir(parents=True, exist_ok=True)

    # Open per-mailbox index.
    conn = _init_db(index_path)

    # Open global index if requested.
    global_conn: sqlite3.Connection | None = None
    if global_index_path:
        g_path = pathlib.Path(global_index_path)
        g_path.parent.mkdir(parents=True, exist_ok=True)
        global_conn = _init_db(g_path)

    try:
        emails = scan_maildir(str(maildir_root))

        emails_indexed = 0
        attachments_indexed = 0

        for email_rec in emails:
            _upsert_email(conn, mailbox, email_rec)
            if global_conn:
                _upsert_email(global_conn, mailbox, email_rec)
            emails_indexed += 1

            parts: list[dict[str, Any]] = email_rec.get("parts", [])
            for part in parts:
                if not _is_extractable_part(part):
                    continue

                _upsert_attachment(conn, part, email_rec["stable_id"], attachment_dir)
                if global_conn:
                    _upsert_attachment(
                        global_conn, part, email_rec["stable_id"], attachment_dir
                    )
                attachments_indexed += 1

        conn.commit()
        if global_conn:
            global_conn.commit()

        # Checkpoint WAL to prevent large lingering WAL files
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
        if global_conn:
            global_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            global_conn.commit()

    finally:
        conn.close()
        if global_conn:
            global_conn.close()

    return IndexResult(
        emails_indexed=emails_indexed,
        attachments_indexed=attachments_indexed,
        index_path=str(index_path.resolve()),
        global_index_path=(
            str(pathlib.Path(global_index_path).resolve())
            if global_index_path
            else None
        ),
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for per-mailbox index building.

    Usage
    -----
    python -m maildir_report.index_mailbox \\
        --mailbox <name> \\
        --data-root <path> \\
        [--global-index <path>]

    Returns
    -------
    int
        Exit code: 0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(
        prog="maildir-index-mailbox",
        description=(
            "Build (or rebuild) the SQLite index for a stored mailbox.\n\n"
            "Reads from <data-root>/maildir/.maildir/ and writes to\n"
            "<data-root>/index.sqlite.\n\n"
            "Idempotent: running multiple times yields identical row counts."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mailbox",
        required=True,
        metavar="NAME",
        help="Mailbox name (stored in every email row; e.g. gabriel.hangel).",
    )
    parser.add_argument(
        "--data-root",
        required=True,
        metavar="PATH",
        help=(
            "Mailbox data directory.  Must contain maildir/.maildir/.\n"
            "index.sqlite will be created here."
        ),
    )
    parser.add_argument(
        "--global-index",
        default=None,
        metavar="PATH",
        help=(
            "Optional path for a global SQLite index populated in addition to "
            "the per-mailbox index (e.g. $DEVENV_ROOT/data/index/mail_index.sqlite)."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress progress output.",
    )

    args = parser.parse_args(argv)

    try:
        result = index_mailbox(
            mailbox=args.mailbox,
            data_root=args.data_root,
            global_index_path=args.global_index,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(
            f"[index-mailbox] emails={result.emails_indexed}"
            f" attachments={result.attachments_indexed}"
            f" index={result.index_path}"
        )
        if result.global_index_path:
            print(f"[index-mailbox] global_index={result.global_index_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

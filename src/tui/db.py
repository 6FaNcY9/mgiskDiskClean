"""
db.py — PyMySQL connector for mail archive TUI.

Handles:
- Connection via unix socket ($MYSQL_UNIX_PORT)
- Query execution with timing + logging
- All queries needed by the TUI (mailboxes, emails, attachments, stats, FTS)
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pymysql
import pymysql.cursors


# ---------------------------------------------------------------------------
# Query log entry
# ---------------------------------------------------------------------------


@dataclass
class QueryLogEntry:
    sql: str
    params: tuple
    rows: int
    elapsed_ms: float
    ts: datetime = field(default_factory=datetime.now)
    error: str | None = None

    def summary(self) -> str:
        short_sql = self.sql.strip().replace("\n", " ")
        if len(short_sql) > 100:
            short_sql = short_sql[:97] + "..."
        status = f"{self.rows} rows" if self.error is None else f"ERR: {self.error}"
        return f"[{self.ts.strftime('%H:%M:%S')}] {self.elapsed_ms:6.1f}ms  {status:12s}  {short_sql}"


# ---------------------------------------------------------------------------
# DB connection wrapper
# ---------------------------------------------------------------------------


class MailDB:
    """Thread-safe-ish PyMySQL wrapper with built-in query logging."""

    MAX_LOG = 500  # ring buffer size

    def __init__(self) -> None:
        self._conn: pymysql.connections.Connection | None = None
        self.log: deque[QueryLogEntry] = deque(maxlen=self.MAX_LOG)
        self.connected = False

    # ── connection ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open connection using $MYSQL_UNIX_PORT or fallback socket path."""
        sock = os.environ.get(
            "MYSQL_UNIX_PORT",
            os.path.join(
                os.environ.get("DEVENV_STATE", ".devenv/state"),
                "mysql.sock",
            ),
        )
        self._conn = pymysql.connect(
            unix_socket=sock,
            user="root",
            database="mailreview",
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=5,
        )
        self.connected = True

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self.connected = False

    def ping(self) -> bool:
        """Return True if connection is alive."""
        try:
            if self._conn:
                self._conn.ping(reconnect=True)
                return True
        except Exception:
            pass
        return False

    # ── query execution ─────────────────────────────────────────────────────

    def execute(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Run a query, log timing, return list of dicts."""
        if not self._conn:
            raise RuntimeError("Not connected")
        t0 = time.perf_counter()
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() or []
            elapsed = (time.perf_counter() - t0) * 1000
            entry = QueryLogEntry(
                sql=sql, params=params, rows=len(rows), elapsed_ms=elapsed
            )
            self.log.append(entry)
            return list(rows)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            entry = QueryLogEntry(
                sql=sql, params=params, rows=0, elapsed_ms=elapsed, error=str(exc)
            )
            self.log.append(entry)
            raise

    def execute_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    # ── mailbox queries ─────────────────────────────────────────────────────

    def list_mailboxes(self) -> list[dict]:
        """Return mailboxes with email+attachment counts and last sync."""
        return self.execute("""
            SELECT
                e.mailbox,
                COUNT(DISTINCT e.stable_id)          AS email_count,
                COUNT(DISTINCT a.sha256)              AS attachment_count,
                MAX(e.imported_at)                   AS last_imported
            FROM archive_emails e
            LEFT JOIN archive_attachments a
                ON a.mailbox = e.mailbox
            GROUP BY e.mailbox
            ORDER BY e.mailbox
        """)

    def mailbox_stats(self, mailbox: str) -> dict | None:
        """Return stats for a single mailbox."""
        return self.execute_one(
            """
            SELECT
                COUNT(DISTINCT e.stable_id)      AS email_count,
                COUNT(DISTINCT a.sha256)         AS attachment_count,
                SUM(e.total_size_bytes)          AS total_bytes,
                MIN(e.date)                      AS oldest_date,
                MAX(e.date)                      AS newest_date,
                MAX(e.imported_at)               AS last_imported
            FROM archive_emails e
            LEFT JOIN archive_attachments a
                ON a.mailbox = e.mailbox AND a.email_stable_id = e.stable_id
            WHERE e.mailbox = %s
        """,
            (mailbox,),
        )

    # ── email list queries ──────────────────────────────────────────────────

    def list_emails(
        self,
        mailbox: str,
        *,
        search: str = "",
        sender_filter: str = "",
        date_from: str = "",
        date_to: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        """List emails with optional FTS + filters."""
        conditions = ["e.mailbox = %s"]
        params: list[Any] = [mailbox]

        if search:
            conditions.append(
                "MATCH(e.subject, e.from_addr, e.to_addrs, e.cc_addrs, e.body_text) "
                "AGAINST(%s IN BOOLEAN MODE)"
            )
            params.append(search)

        if sender_filter:
            conditions.append("e.from_addr LIKE %s")
            params.append(f"%{sender_filter}%")

        if date_from:
            conditions.append("e.date >= %s")
            params.append(date_from)

        if date_to:
            conditions.append("e.date <= %s")
            params.append(date_to)

        where = " AND ".join(conditions)
        order = (
            "MATCH(e.subject, e.from_addr, e.to_addrs, e.cc_addrs, e.body_text) "
            "AGAINST(%s IN BOOLEAN MODE) DESC, e.date DESC"
            if search
            else "e.date DESC"
        )
        if search:
            params.append(search)

        params += [limit, offset]
        return self.execute(
            f"""
            SELECT
                e.stable_id,
                e.from_addr,
                e.subject,
                e.date,
                e.folder,
                e.total_size_bytes,
                e.imported_at,
                (SELECT COUNT(*) FROM archive_attachments a
                 WHERE a.mailbox = e.mailbox AND a.email_stable_id = e.stable_id
                ) AS attachment_count
            FROM archive_emails e
            WHERE {where}
            ORDER BY {order}
            LIMIT %s OFFSET %s
        """,
            tuple(params),
        )

    def count_emails(
        self,
        mailbox: str,
        *,
        search: str = "",
        sender_filter: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> int:
        """Count emails matching filters."""
        conditions = ["mailbox = %s"]
        params: list[Any] = [mailbox]

        if search:
            conditions.append(
                "MATCH(subject, from_addr, to_addrs, cc_addrs, body_text) "
                "AGAINST(%s IN BOOLEAN MODE)"
            )
            params.append(search)

        if sender_filter:
            conditions.append("from_addr LIKE %s")
            params.append(f"%{sender_filter}%")

        if date_from:
            conditions.append("date >= %s")
            params.append(date_from)

        if date_to:
            conditions.append("date <= %s")
            params.append(date_to)

        where = " AND ".join(conditions)
        row = self.execute_one(
            f"SELECT COUNT(*) AS n FROM archive_emails WHERE {where}",
            tuple(params),
        )
        return int(row["n"]) if row else 0

    # ── email detail ────────────────────────────────────────────────────────

    def get_email(self, mailbox: str, stable_id: str) -> dict | None:
        """Fetch full email record including body."""
        return self.execute_one(
            """
            SELECT *
            FROM archive_emails
            WHERE mailbox = %s AND stable_id = %s
        """,
            (mailbox, stable_id),
        )

    def get_attachments(self, mailbox: str, stable_id: str) -> list[dict]:
        """Fetch attachments for one email."""
        return self.execute(
            """
            SELECT stored_path, original_filename, mime, size, sha256
            FROM archive_attachments
            WHERE mailbox = %s AND email_stable_id = %s
            ORDER BY original_filename
        """,
            (mailbox, stable_id),
        )

    # ── new-since-last-sync detection ────────────────────────────────────────

    def get_recent_stable_ids(self, mailbox: str, since: datetime) -> set[str]:
        """Return stable_ids imported after `since` for highlight logic."""
        rows = self.execute(
            """
            SELECT stable_id
            FROM archive_emails
            WHERE mailbox = %s AND imported_at >= %s
        """,
            (mailbox, since.strftime("%Y-%m-%d %H:%M:%S")),
        )
        return {r["stable_id"] for r in rows}

    # ── query log access ────────────────────────────────────────────────────

    def log_lines(self) -> list[str]:
        """Return formatted log lines (newest first)."""
        return [e.summary() for e in reversed(self.log)]

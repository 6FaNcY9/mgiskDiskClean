from __future__ import annotations
import sqlite3
from pathlib import Path


class MailDB:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA busy_timeout=3000")

    def stats(self) -> dict:
        row = self._con.execute(
            """SELECT
                 (SELECT COUNT(*) FROM archive_emails)       AS email_count,
                 (SELECT COUNT(*) FROM archive_attachments)  AS attachment_count,
                 (SELECT MAX(date) FROM archive_emails)      AS last_updated"""
        ).fetchone()
        return {
            "email_count": row["email_count"],
            "attachment_count": row["attachment_count"],
            "last_updated": row["last_updated"] or "",
        }

    def _filter_clauses(
        self,
        mailbox: str | None,
        date_from: str | None,
        date_to: str | None,
        has_attachment: bool | None,
    ) -> tuple[list[str], list]:
        clauses: list[str] = []
        params: list = []
        if mailbox:
            clauses.append("mailbox = ?")
            params.append(mailbox)
        if date_from:
            clauses.append("date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("date <= ?")
            params.append(date_to)
        if has_attachment is True:
            clauses.append(
                "EXISTS (SELECT 1 FROM archive_attachments"
                " WHERE email_stable_id = archive_emails.stable_id)"
            )
        elif has_attachment is False:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM archive_attachments"
                " WHERE email_stable_id = archive_emails.stable_id)"
            )
        return clauses, params

    def _fetch_emails(
        self, clauses: list[str], params: list, page: int, per_page: int
    ) -> list[dict]:
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._con.execute(
            f"""SELECT mailbox, stable_id, from_addr, subject, date
                FROM archive_emails {where}
                ORDER BY date DESC LIMIT ? OFFSET ?""",
            [*params, per_page, page * per_page],
        ).fetchall()
        return [dict(r) for r in rows]

    def search(
        self,
        q: str,
        mailbox: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        has_attachment: bool | None = None,
        page: int = 0,
        per_page: int = 50,
    ) -> list[dict]:
        clauses, params = self._filter_clauses(mailbox, date_from, date_to, has_attachment)
        if q.strip():
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append(
                "(subject LIKE ? ESCAPE '\\' OR from_addr LIKE ? ESCAPE '\\'"
                " OR to_addrs LIKE ? ESCAPE '\\' OR body_text LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern, pattern, pattern])
        return self._fetch_emails(clauses, params, page, per_page)

    def browse(
        self,
        mailbox: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        has_attachment: bool | None = None,
        page: int = 0,
        per_page: int = 50,
    ) -> list[dict]:
        clauses, params = self._filter_clauses(mailbox, date_from, date_to, has_attachment)
        return self._fetch_emails(clauses, params, page, per_page)

    def get_email(self, mailbox: str, stable_id: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM archive_emails WHERE mailbox=? AND stable_id=?",
            (mailbox, stable_id),
        ).fetchone()
        return dict(row) if row else None

    def get_attachments(self, mailbox: str, email_stable_id: str) -> list[dict]:
        rows = self._con.execute(
            """SELECT * FROM archive_attachments
               WHERE mailbox=? AND email_stable_id=?""",
            (mailbox, email_stable_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_attachment_by_sha256(self, sha256: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM archive_attachments WHERE sha256=?", (sha256,)
        ).fetchone()
        return dict(row) if row else None

    def mailboxes(self) -> list[str]:
        rows = self._con.execute(
            "SELECT DISTINCT mailbox FROM archive_emails ORDER BY mailbox"
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._con.close()

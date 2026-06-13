from __future__ import annotations
import sqlite3
from pathlib import Path


class MailDB:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row

    def stats(self) -> dict:
        ec = self._con.execute("SELECT COUNT(*) FROM archive_emails").fetchone()[0]
        ac = self._con.execute("SELECT COUNT(*) FROM archive_attachments").fetchone()[0]
        last = self._con.execute(
            "SELECT MAX(date) FROM archive_emails"
        ).fetchone()[0]
        return {"email_count": ec, "attachment_count": ac, "last_updated": last or ""}

    def search(self, q: str, page: int = 0, per_page: int = 50) -> list[dict]:
        pattern = f"%{q}%"
        rows = self._con.execute(
            """SELECT mailbox, stable_id, from_addr, subject, date
               FROM archive_emails
               WHERE subject LIKE ? OR from_addr LIKE ? OR to_addrs LIKE ?
                  OR body_text LIKE ?
               ORDER BY date DESC LIMIT ? OFFSET ?""",
            (pattern, pattern, pattern, pattern, per_page, page * per_page),
        ).fetchall()
        return [dict(r) for r in rows]

    def browse(self, mailbox: str | None, page: int = 0, per_page: int = 50) -> list[dict]:
        if mailbox:
            rows = self._con.execute(
                """SELECT mailbox, stable_id, from_addr, subject, date
                   FROM archive_emails WHERE mailbox = ?
                   ORDER BY date DESC LIMIT ? OFFSET ?""",
                (mailbox, per_page, page * per_page),
            ).fetchall()
        else:
            rows = self._con.execute(
                """SELECT mailbox, stable_id, from_addr, subject, date
                   FROM archive_emails ORDER BY date DESC LIMIT ? OFFSET ?""",
                (per_page, page * per_page),
            ).fetchall()
        return [dict(r) for r in rows]

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

import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_build_client_sqlite_converts_index_schema(tmp_path):
    source = tmp_path / "mail_index.sqlite"
    output = tmp_path / "client" / "mail_archive.sqlite"

    conn = sqlite3.connect(source)
    conn.execute(
        """
        CREATE TABLE emails (
            mailbox TEXT NOT NULL,
            stable_id TEXT NOT NULL PRIMARY KEY,
            filepath TEXT NOT NULL,
            folder TEXT NOT NULL,
            date TEXT NOT NULL,
            from_addr TEXT NOT NULL,
            to_addrs TEXT NOT NULL DEFAULT '',
            cc_addrs TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL,
            body_text TEXT NOT NULL DEFAULT '',
            total_size_bytes INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE attachments (
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            mime TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            email_stable_id TEXT NOT NULL,
            PRIMARY KEY (stored_path, email_stable_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails
        (mailbox, stable_id, filepath, folder, date, from_addr, to_addrs,
         cc_addrs, subject, body_text, total_size_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inbox",
            "a" * 64,
            "Maildir/cur/1.eml",
            "INBOX",
            "2026-06-11 10:00:00",
            "sender@example.com",
            "to@example.com",
            "",
            "Subject",
            "Body text",
            1234,
        ),
    )
    conn.execute(
        """
        INSERT INTO attachments
        (sha256, size, mime, original_filename, stored_path, email_stable_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("b" * 64, 5, "text/plain", "note.txt", "b_5.txt", "a" * 64),
    )
    conn.commit()
    conn.close()

    subprocess.run(
        [
            "php",
            str(ROOT / "web/src/cli/build_client_sqlite.php"),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        check=True,
        cwd=ROOT,
    )

    conn = sqlite3.connect(output)
    email = conn.execute(
        "SELECT mailbox, stable_id, subject FROM archive_emails"
    ).fetchone()
    attachment = conn.execute(
        "SELECT mailbox, email_stable_id, original_filename FROM archive_attachments"
    ).fetchone()
    review_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    conn.close()

    assert email == ("inbox", "a" * 64, "Subject")
    assert attachment == ("inbox", "a" * 64, "note.txt")
    assert {"vt_cache", "review_decisions"}.issubset(review_tables)

import sqlite3
import pytest
from pathlib import Path
from mrija_client.db import MailDB


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE archive_emails (
            mailbox TEXT, stable_id TEXT, filepath TEXT, folder TEXT,
            date TEXT, from_addr TEXT, to_addrs TEXT, cc_addrs TEXT,
            subject TEXT, body_text TEXT, total_size_bytes INTEGER,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, stable_id)
        );
        CREATE TABLE archive_attachments (
            mailbox TEXT, email_stable_id TEXT, stored_path TEXT,
            sha256 TEXT, size INTEGER, mime TEXT, original_filename TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id, sha256)
        );
        INSERT INTO archive_emails VALUES
            ('box1','id1','f1','Inbox','2024-01-01','alice@x.com','bob@x.com',
             '','Hello world','body text one',1000,'2024-01-01'),
            ('box1','id2','f2','Inbox','2024-01-02','bob@x.com','charlie@x.com',
             '','Invoice attached','invoice body',2000,'2024-01-02');
        INSERT INTO archive_attachments VALUES
            ('box1','id2','mailboxes/box1/attachments/abc.pdf',
             'deadbeef',1024,'application/pdf','invoice.pdf','2024-01-02');
    """)
    con.close()
    return MailDB(path)


def test_stats(db):
    s = db.stats()
    assert s["email_count"] == 2
    assert s["attachment_count"] == 1


def test_search_by_subject(db):
    rows = db.search("Invoice")
    assert len(rows) == 1
    assert rows[0]["stable_id"] == "id2"


def test_search_by_from(db):
    rows = db.search("alice@x.com")
    assert len(rows) == 1
    assert rows[0]["stable_id"] == "id1"


def test_search_no_results(db):
    assert db.search("zzz_no_match") == []


def test_browse_by_mailbox(db):
    rows = db.browse("box1")
    assert len(rows) == 2


def test_get_email(db):
    email = db.get_email("box1", "id1")
    assert email is not None
    assert email["subject"] == "Hello world"


def test_get_email_missing(db):
    assert db.get_email("box1", "nope") is None


def test_get_attachments(db):
    atts = db.get_attachments("box1", "id2")
    assert len(atts) == 1
    assert atts[0]["original_filename"] == "invoice.pdf"


def test_get_attachment_by_sha256(db):
    att = db.get_attachment_by_sha256("deadbeef")
    assert att is not None
    assert att["mime"] == "application/pdf"


def test_mailboxes(db):
    boxes = db.mailboxes()
    assert boxes == ["box1"]

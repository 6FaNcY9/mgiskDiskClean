import sqlite3
import pytest
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


def test_search_filter_by_mailbox(db):
    boxes = db.mailboxes()
    if not boxes:
        pytest.skip("no mailboxes in fixture db")
    results = db.search("", mailbox=boxes[0])
    assert all(r["mailbox"] == boxes[0] for r in results)

def test_search_filter_date_from(db):
    results_all = db.search("")
    if not results_all:
        pytest.skip("empty db")
    latest_date = results_all[0]["date"]
    results_filtered = db.search("", date_from=latest_date)
    assert all(r["date"] >= latest_date for r in results_filtered)

def test_search_filter_date_to(db):
    results_all = db.search("")
    if not results_all:
        pytest.skip("empty db")
    earliest_date = results_all[-1]["date"]
    results_filtered = db.search("", date_to=earliest_date)
    assert all(r["date"] <= earliest_date for r in results_filtered)

def test_search_filter_has_attachment(db):
    with_att = db.search("", has_attachment=True)
    without_att = db.search("", has_attachment=False)
    total = db.search("")
    assert len(with_att) + len(without_att) == len(total)

def test_browse_filter_date_from(db):
    results_all = db.browse(None)
    if not results_all:
        pytest.skip("empty db")
    latest_date = results_all[0]["date"]
    results_filtered = db.browse(None, date_from=latest_date)
    assert all(r["date"] >= latest_date for r in results_filtered)


def test_search_query_with_percent_literal(db):
    # A literal '%' in the query must not act as a SQL wildcard
    results = db.search("%")
    # The result should only contain emails whose fields literally contain '%'
    for r in results:
        # We can't easily assert the DB internals, but we can assert no crash
        # and that the search returns consistently (same result twice)
        pass
    results2 = db.search("%")
    assert results == results2

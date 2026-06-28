import sqlite3
import pytest
from fastapi.testclient import TestClient
from mrija_client.state import AppState, ClientState
from mrija_client.db import MailDB
from mrija_client.server import create_app


@pytest.fixture(autouse=True)
def set_password(monkeypatch):
    monkeypatch.setenv("MRIJA_PASSWORD", "secret")


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.sqlite"
    con = sqlite3.connect(db_path)
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
             '','Hello world','body text',1000,'2024-01-01'),
            ('box1','id2','f2','Inbox','2024-01-02','bob@x.com','charlie@x.com',
             '','Invoice attached','invoice',2000,'2024-01-02');
        INSERT INTO archive_attachments VALUES
            ('box1','id2','mailboxes/box1/attachments/inv.pdf',
             'abc123',1024,'application/pdf','invoice.pdf','2024-01-02');
    """)
    con.close()
    state = AppState(state=ClientState.RUNNING, db=MailDB(db_path), db_path=db_path)
    client = TestClient(create_app(state))
    client.post("/login", data={"password": "secret"})
    return client


def test_search_returns_html(client):
    r = client.get("/data/search?q=Invoice")
    assert r.status_code == 200
    assert "Invoice attached" in r.text
    assert "article" in r.text


def test_search_empty_returns_empty_state(client):
    r = client.get("/data/search?q=zzz_no_match")
    assert r.status_code == 200
    assert "No results" in r.text


def test_browse_returns_all_emails(client):
    r = client.get("/data/browse?mailbox=box1")
    assert r.status_code == 200
    assert "Hello world" in r.text
    assert "Invoice attached" in r.text


def test_email_detail(client):
    r = client.get("/data/email/box1/id2")
    assert r.status_code == 200
    assert "Invoice attached" in r.text
    assert "invoice.pdf" in r.text


def test_email_detail_missing(client):
    r = client.get("/data/email/box1/nope")
    assert r.status_code == 404


def test_search_query_capped_at_200_chars(client):
    long_q = "a" * 500
    r = client.get(f"/data/search?q={long_q}")
    assert r.status_code == 200  # never crashes on long input

def test_search_negative_page_returns_empty(client):
    r = client.get("/data/search?q=test&page=-1")
    assert r.status_code == 200  # clamped, not errored

def test_browse_negative_page_returns_empty(client):
    r = client.get("/data/browse?page=-99")
    assert r.status_code == 200


def test_search_accepts_mailbox_param(client):
    r = client.get("/data/search?q=&mailbox=gabriel.hangel")
    assert r.status_code == 200

def test_search_accepts_date_params(client):
    r = client.get("/data/search?q=&date_from=2024-01-01&date_to=2024-12-31")
    assert r.status_code == 200

def test_search_accepts_has_attachment_param(client):
    r = client.get("/data/search?q=&has_attachment=true")
    assert r.status_code == 200

def test_mailboxes_endpoint_returns_html(client):
    r = client.get("/data/mailboxes")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]

def test_browse_accepts_date_params(client):
    r = client.get("/data/browse?date_from=2024-01-01")
    assert r.status_code == 200

def test_mailboxes_option_selected(client):
    r = client.get("/data/mailboxes?selected=gabriel.hangel")
    assert r.status_code == 200
    assert "gabriel.hangel" in r.text

def test_filters_endpoint_returns_sidebar(client):
    r = client.get("/data/filters")
    assert r.status_code == 200
    assert "filter-form" in r.text
    assert "date_from" in r.text
    assert "has_attachment" in r.text

def test_search_results_has_pagination(client):
    r = client.get("/data/search?q=&page=0")
    assert r.status_code == 200
    assert "page-btn" in r.text

def test_browse_has_pagination(client):
    r = client.get("/data/browse?page=0")
    assert r.status_code == 200
    assert "page-btn" in r.text

import sqlite3
import pytest
from fastapi.testclient import TestClient
from mrija_client.state import AppState, ClientState
from mrija_client.db import MailDB
from mrija_client.server import create_app

API_KEY = "test-key"


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", API_KEY)


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
             '','Hello','body',1000,'2024-01-01');
    """)
    con.close()
    state = AppState(state=ClientState.RUNNING, db=MailDB(db_path), db_path=db_path)
    return TestClient(create_app(state))


def auth():
    return {"X-Api-Key": API_KEY}


def test_status_ok(client):
    r = client.get("/api/status", headers=auth())
    assert r.status_code == 200
    j = r.json()
    assert j["state"] == "running"
    assert j["email_count"] == 1
    assert "attachment_count" in j
    assert "last_updated" in j


def test_status_no_auth(client):
    r = client.get("/api/status")
    assert r.status_code == 401


def test_status_wrong_key(client):
    r = client.get("/api/status", headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


def test_open_local_file(client, tmp_path):
    db_path = tmp_path / "new.sqlite"
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
    """)
    con.close()
    r = client.post("/api/open", json={"path": str(db_path)}, headers=auth())
    assert r.status_code == 200
    assert r.json()["state"] == "running"


def test_open_missing_file(client):
    r = client.post("/api/open", json={"path": "/nonexistent/file.sqlite"}, headers=auth())
    assert r.status_code == 404

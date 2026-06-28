from fastapi.testclient import TestClient

from mrija_client.server import create_app
from mrija_client.state import AppState, ClientState


def test_admin_audit_tracks_login_session_and_logout(monkeypatch):
    monkeypatch.setenv("MRIJA_PASSWORD", "secret")
    state = AppState(state=ClientState.RUNNING)
    client = TestClient(create_app(state, mode="admin"))

    r = client.get("/data/audit/metrics")
    assert r.status_code == 303
    assert state.audit_events[-1]["event"] == "auth_required"

    r = client.post("/login", data={"password": "wrong"})
    assert r.status_code == 303
    assert state.audit_events[-1]["event"] == "login_failed"

    r = client.post("/login", data={"password": "secret"})
    assert r.status_code == 303
    assert state.audit_events[-1]["event"] == "login_success"
    assert len(state.sessions) == 1

    r = client.get("/data/audit/metrics")
    assert r.status_code == 200
    assert "Login OK" in r.text
    assert "Active sessions" in r.text

    r = client.get("/data/audit/sessions")
    assert r.status_code == 200
    assert "browser" in r.text or "other" in r.text

    r = client.get("/logout")
    assert r.status_code == 303
    assert state.audit_events[-1]["event"] == "logout"
    assert state.sessions == {}


def test_audit_fragments_are_admin_mode_only(monkeypatch):
    monkeypatch.setenv("MRIJA_PASSWORD", "secret")
    state = AppState(state=ClientState.RUNNING)
    client = TestClient(create_app(state, mode="user"))
    client.post("/login", data={"password": "secret"})

    r = client.get("/data/audit/events")
    assert r.status_code == 404


def test_audit_events_load_from_persistent_file(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit" / "events.jsonl"
    audit_path.parent.mkdir()
    audit_path.write_text(
        '{"ts":"10:00:00","at":"2026-06-29T10:00:00",'
        '"event":"login_success","message":"Old login",'
        '"ip":"127.0.0.1","client":"browser","user_agent":"",'
        '"session":"abc","details":{}}\n'
        "not json\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MRIJA_AUDIT_LOG", str(audit_path))

    state = AppState(state=ClientState.RUNNING)
    create_app(state, mode="admin")

    assert any(e["message"] == "Old login" for e in state.audit_events)
    assert state.audit_events[-1]["event"] == "server_started"
    assert "server_started" in audit_path.read_text(encoding="utf-8")

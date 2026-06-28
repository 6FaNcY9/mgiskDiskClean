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

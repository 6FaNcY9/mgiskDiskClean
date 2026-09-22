from fastapi.testclient import TestClient
import pytest

from mrija_client.credentials import CredentialError, read_credential
from mrija_client.server import create_app
from mrija_client.state import AppState


def test_read_file_credential_removes_one_newline(tmp_path, monkeypatch):
    path = tmp_path / "secret"
    path.write_text("password  \n\n", encoding="utf-8")
    monkeypatch.setenv("MRIJA_PASSWORD_FILE", str(path))

    assert read_credential("MRIJA_PASSWORD") == "password  \n"


def test_read_direct_credential_compatibility(monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", "direct")

    assert read_credential("MRIJA_API_KEY") == "direct"


@pytest.mark.parametrize("contents", ["", "\n"])
def test_empty_file_fails_closed(tmp_path, monkeypatch, contents):
    path = tmp_path / "secret"
    path.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("MRIJA_PASSWORD_FILE", str(path))

    with pytest.raises(CredentialError):
        read_credential("MRIJA_PASSWORD")


def test_missing_file_fails_closed(monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY_FILE", "/does/not/exist")

    with pytest.raises(CredentialError):
        read_credential("MRIJA_API_KEY")


def test_nonempty_direct_and_file_conflict(tmp_path, monkeypatch):
    path = tmp_path / "secret"
    path.write_text("file-key", encoding="utf-8")
    monkeypatch.setenv("MRIJA_API_KEY", "direct-key")
    monkeypatch.setenv("MRIJA_API_KEY_FILE", str(path))

    with pytest.raises(CredentialError):
        read_credential("MRIJA_API_KEY")


def test_api_file_auth_and_invalid_configuration_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "api-key"
    path.write_text("file-key\n", encoding="utf-8")
    monkeypatch.delenv("MRIJA_API_KEY", raising=False)
    monkeypatch.setenv("MRIJA_API_KEY_FILE", str(path))
    client = TestClient(create_app(AppState()))

    assert client.get("/api/status", headers={"X-Api-Key": "file-key"}).status_code == 200
    assert client.get("/api/status", headers={"X-Api-Key": "wrong"}).status_code == 401

    path.unlink()
    assert client.get("/api/status", headers={"X-Api-Key": "file-key"}).status_code == 401


def test_password_file_login(tmp_path, monkeypatch):
    path = tmp_path / "password"
    path.write_text("p ass  \n", encoding="utf-8")
    monkeypatch.delenv("MRIJA_PASSWORD", raising=False)
    monkeypatch.setenv("MRIJA_PASSWORD_FILE", str(path))
    client = TestClient(create_app(AppState()))

    assert client.post("/login", data={"password": "p ass  "}, follow_redirects=False).status_code == 303
    assert client.post("/login", data={"password": "p ass"}, follow_redirects=False).headers["location"] == "/login?error=1"

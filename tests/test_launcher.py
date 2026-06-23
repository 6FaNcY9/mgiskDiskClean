"""
Unit tests for launcher/windows/app.py (thin pywebview wrapper).
All network and OS calls are mocked so tests run on Linux without webview.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.modules.setdefault("webview", MagicMock())

sys.path.insert(0, str(Path(__file__).parent.parent / "launcher" / "windows"))
import app as launcher


# ── _find_db ──────────────────────────────────────────────────────────────────

def test_find_db_returns_none_when_data_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_DATA", tmp_path / "nonexistent")
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [])
    assert launcher._find_db() is None


def test_find_db_returns_none_when_dir_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_DATA", tmp_path)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [])
    assert launcher._find_db() is None


def test_find_db_returns_newest_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_DATA", tmp_path)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [])
    old = tmp_path / "old.sqlite"
    new = tmp_path / "new.sqlite"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    assert launcher._find_db() == new


def test_find_db_ignores_non_sqlite_files(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_DATA", tmp_path)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [])
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "readme.md").write_text("also ignore")
    assert launcher._find_db() is None


def test_find_db_single_file(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_DATA", tmp_path)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [])
    db = tmp_path / "archive.sqlite"
    db.write_bytes(b"db")
    assert launcher._find_db() == db


def test_find_db_installs_bundled_client_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "appdata" / "MrijaArchive" / "data" / "client"
    bundle = tmp_path / "bundle"
    bundled = bundle / "data" / "client" / "mail_archive.sqlite"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"bundled")

    monkeypatch.setattr(launcher, "_DATA", data_dir)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [bundle])

    found = launcher._find_db()

    assert found == data_dir / "mail_archive.sqlite"
    assert found.read_bytes() == b"bundled"


def test_find_db_installs_legacy_bundled_index_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "appdata" / "MrijaArchive" / "data" / "client"
    bundle = tmp_path / "bundle"
    bundled = bundle / "data" / "index" / "mail_index.sqlite"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"legacy")

    monkeypatch.setattr(launcher, "_DATA", data_dir)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [bundle])

    found = launcher._find_db()

    assert found == data_dir / "mail_archive.sqlite"
    assert found.read_bytes() == b"legacy"


def test_find_db_prefers_canonical_bundled_client_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "appdata" / "MrijaArchive" / "data" / "client"
    bundle = tmp_path / "bundle"
    canonical = bundle / "data" / "client" / "mail_archive.sqlite"
    legacy = bundle / "data" / "index" / "mail_index.sqlite"
    canonical.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical")
    legacy.write_bytes(b"legacy")
    os.utime(canonical, (1_000_000, 1_000_000))
    os.utime(legacy, (2_000_000, 2_000_000))

    monkeypatch.setattr(launcher, "_DATA", data_dir)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [bundle])

    found = launcher._find_db()

    assert found == data_dir / "mail_archive.sqlite"
    assert found.read_bytes() == b"canonical"


def test_bundle_roots_excludes_cwd_for_plain_python(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    roots = launcher._bundle_roots()
    assert tmp_path.resolve() not in roots


def test_find_db_keeps_newer_installed_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "appdata" / "MrijaArchive" / "data" / "client"
    data_dir.mkdir(parents=True)
    installed = data_dir / "mail_archive.sqlite"
    installed.write_bytes(b"installed")
    os.utime(installed, (2_000_000, 2_000_000))

    bundle = tmp_path / "bundle"
    bundled = bundle / "data" / "client" / "mail_archive.sqlite"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"bundled")
    os.utime(bundled, (1_000_000, 1_000_000))

    monkeypatch.setattr(launcher, "_DATA", data_dir)
    monkeypatch.setattr(launcher, "_bundle_roots", lambda: [bundle])

    assert launcher._find_db() == installed
    assert installed.read_bytes() == b"installed"


def test_package_scripts_use_canonical_client_db_path():
    root = Path(__file__).parent.parent
    package_bat = (root / "launcher/windows/package.bat").read_text()
    update_sh = (root / "launcher/windows/package-data-update.sh").read_text()

    assert "data\\client\\mail_archive.sqlite" in package_bat
    assert "CLIENT_DB" in package_bat
    assert "data/client/mail_archive.sqlite" in update_sh


# ── _wait ─────────────────────────────────────────────────────────────────────

def test_wait_returns_true_on_success(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: MagicMock())
    assert launcher._wait("http://localhost:9999", timeout=2.0) is True


def test_wait_returns_false_on_timeout(monkeypatch):
    def _always_fail(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _always_fail)
    assert launcher._wait("http://localhost:9999", timeout=0.3) is False


# ── _Api.open_file ────────────────────────────────────────────────────────────

class _FakeWindow:
    def __init__(self, chosen=None):
        self._chosen    = chosen
        self.loaded_url = None
        self.js_calls: list[str] = []

    def create_file_dialog(self, *a, **kw):
        return [self._chosen] if self._chosen else None

    def load_url(self, url):
        self.loaded_url = url

    def evaluate_js(self, js: str) -> None:
        self.js_calls.append(js)


def test_api_save_attachment_rejects_empty_sha256():
    api = launcher._Api(MagicMock())
    assert api.save_attachment("", "x.txt") == {"error": "Invalid attachment ID."}


def test_api_save_attachment_rejects_short_sha256():
    api = launcher._Api(MagicMock())
    assert api.save_attachment("abc123", "x.txt") == {"error": "Invalid attachment ID."}


def test_api_save_attachment_rejects_non_hex_sha256():
    api = launcher._Api(MagicMock())
    bad = "g" * 64
    assert api.save_attachment(bad, "x.txt") == {"error": "Invalid attachment ID."}


def test_api_save_attachment_rejects_oversized_response(monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", "key")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=-1):
            return b"x" * (launcher._MAX_ATTACHMENT_BYTES + 1)

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _Response())

    api = launcher._Api(MagicMock())
    sha = "a" * 64
    assert api.save_attachment(sha, "x.txt") == {
        "error": "Attachment is too large to save."
    }


def test_api_open_file_cancels_gracefully():
    api      = launcher._Api(MagicMock())
    api._win = _FakeWindow(chosen=None)
    api.open_file()  # should not raise


def test_api_open_file_posts_path_to_api_open(tmp_path, monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", "test-key")

    posted = []

    def fake_urlopen(req, timeout=5):
        posted.append({
            "url":  req.full_url,
            "body": json.loads(req.data),
            "key":  req.get_header("X-api-key"),
        })
        m = MagicMock()
        m.__enter__ = lambda s: s
        m.__exit__  = MagicMock(return_value=False)
        return m

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    chosen = tmp_path / "archive.sqlite"
    chosen.write_bytes(b"db")

    api      = launcher._Api(MagicMock())
    api._win = _FakeWindow(chosen=str(chosen))
    api.open_file()

    assert len(posted) == 1
    assert "/api/open" in posted[0]["url"]
    assert posted[0]["body"]["path"] == str(chosen)
    assert posted[0]["key"] == "test-key"


def test_api_open_file_loads_url_after_success(tmp_path, monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", "key")

    def fake_urlopen(req, timeout=5):
        m = MagicMock()
        m.__enter__ = lambda s: s
        m.__exit__  = MagicMock(return_value=False)
        return m

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    chosen = tmp_path / "archive.sqlite"
    chosen.write_bytes(b"db")

    win      = _FakeWindow(chosen=str(chosen))
    api      = launcher._Api(MagicMock())
    api._win = win
    api.open_file()

    assert win.loaded_url == launcher._URL


def test_api_open_file_silent_on_network_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", "key")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("refused")))

    chosen = tmp_path / "archive.sqlite"
    chosen.write_bytes(b"db")

    api      = launcher._Api(MagicMock())
    api._win = _FakeWindow(chosen=str(chosen))
    api.open_file()  # should not raise


def test_api_open_file_uses_api_key_header(tmp_path, monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", "secret-key-123")

    captured_key = []

    def fake_urlopen(req, timeout=5):
        captured_key.append(req.get_header("X-api-key"))
        m = MagicMock()
        m.__enter__ = lambda s: s
        m.__exit__  = MagicMock(return_value=False)
        return m

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    chosen = tmp_path / "archive.sqlite"
    chosen.write_bytes(b"db")

    api      = launcher._Api(MagicMock())
    api._win = _FakeWindow(chosen=str(chosen))
    api.open_file()

    assert captured_key == ["secret-key-123"]

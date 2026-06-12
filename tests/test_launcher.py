"""
Unit tests for launcher/windows/app.py core functions.
All subprocess and OS calls are mocked so tests run on Linux.
"""
import sys
import json
import hashlib
import zipfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest

sys.modules.setdefault("webview", MagicMock())

# Point to launcher source
sys.path.insert(0, str(Path(__file__).parent.parent / "launcher" / "windows"))
import app as launcher


# ── extract_app_bundle ────────────────────────────────────────────────────────

def test_extract_app_bundle_first_run(tmp_path, monkeypatch):
    """Bundle is extracted when docker-compose.yml doesn't exist yet."""
    app_dir = tmp_path / "MrijaArchive"
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    bundle = tmp_path / "app_bundle.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("docker-compose.yml", "services: {}")
    monkeypatch.setattr(launcher, "BUNDLE_ZIP", bundle)

    launcher.extract_app_bundle()

    assert (app_dir / "docker-compose.yml").exists()


def test_extract_app_bundle_skips_if_exists(tmp_path, monkeypatch):
    """Bundle extraction is skipped when docker-compose.yml already present."""
    app_dir = tmp_path / "MrijaArchive"
    app_dir.mkdir()
    compose = app_dir / "docker-compose.yml"
    compose.write_text("existing")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    bundle = tmp_path / "app_bundle.zip"
    monkeypatch.setattr(launcher, "BUNDLE_ZIP", bundle)

    launcher.extract_app_bundle()

    assert compose.read_text() == "existing"


# ── copy_data ─────────────────────────────────────────────────────────────────

def test_copy_data_copies_on_first_run(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    app_dir.mkdir()
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    src = tmp_path / "data" / "index"
    src.mkdir(parents=True)
    (src / "mail_index.sqlite").write_bytes(b"SQLITEDATA")
    monkeypatch.setattr(launcher, "DATA_SRC", tmp_path / "data")

    launcher.copy_data()

    assert (app_dir / "data" / "index" / "mail_index.sqlite").read_bytes() == b"SQLITEDATA"


def test_copy_data_skips_if_sqlite_exists(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    data_dir = app_dir / "data"
    data_dir.mkdir(parents=True)
    marker = data_dir / "marker.txt"
    marker.write_text("EXISTING")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    src = tmp_path / "data"
    monkeypatch.setattr(launcher, "DATA_SRC", src)

    launcher.copy_data()

    assert marker.read_text() == "EXISTING"


# ── PHP runtime ───────────────────────────────────────────────────────────────

def test_find_php_exe_prefers_bundled_php(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    bundled = app_dir / "php" / "php.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("php")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/php")

    assert launcher.find_php_exe() == bundled


def test_find_php_exe_falls_back_to_path(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    app_dir.mkdir()
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/php" if name == "php" else None)
    monkeypatch.setattr(launcher, "PHP_SRC", tmp_path / "nonexistent_php")

    result = launcher.find_php_exe()
    assert result == Path("/usr/bin/php")


def test_find_php_exe_returns_none_when_missing(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    app_dir.mkdir()
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(launcher, "PHP_SRC", tmp_path / "nonexistent_php")

    assert launcher.find_php_exe() is None


def test_write_client_config_installs_local_php(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    config_dir = app_dir / "web" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "local.php.client").write_text("client config")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    launcher.write_client_config()

    assert (config_dir / "local.php").read_text() == "client config"


def test_build_client_database_runs_converter(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    source = app_dir / "data" / "index" / "mail_index.sqlite"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"sqlite")
    script = app_dir / "web" / "src" / "cli" / "build_client_sqlite.php"
    script.parent.mkdir(parents=True)
    script.write_text("<?php")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        output = app_dir / "data" / "client" / "mail_archive.sqlite"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"client")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = launcher.build_client_database(tmp_path / "php.exe")

    assert result == app_dir / "data" / "client" / "mail_archive.sqlite"
    assert calls
    assert "--source" in calls[0][0]
    assert "--output" in calls[0][0]


def test_build_client_database_skips_if_exists(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    output = app_dir / "data" / "client" / "mail_archive.sqlite"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"existing")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: calls.append(a))

    result = launcher.build_client_database(tmp_path / "php.exe")

    assert result == output
    assert not calls  # no subprocess call


def test_build_client_database_force_rebuilds(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    source = app_dir / "data" / "index" / "mail_index.sqlite"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"sqlite")
    output = app_dir / "data" / "client" / "mail_archive.sqlite"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"old")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        output.write_bytes(b"new")

    monkeypatch.setattr("subprocess.run", fake_run)

    launcher.build_client_database(tmp_path / "php.exe", force=True)

    assert calls  # subprocess was called despite output existing


def test_build_client_database_raises_when_no_source(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    app_dir.mkdir()
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    with pytest.raises(FileNotFoundError, match="No archive data found"):
        launcher.build_client_database(tmp_path / "php.exe")


def test_start_php_server_binds_localhost(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    (app_dir / "web" / "public").mkdir(parents=True)
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    calls = []

    class FakeProc:
        def terminate(self):
            pass

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    proc = launcher.start_php_server(
        tmp_path / "php.exe",
        app_dir / "data" / "client" / "mail_archive.sqlite",
    )

    assert isinstance(proc, FakeProc)
    assert "127.0.0.1:8080" in calls[0][0]
    assert calls[0][1]["env"]["MRIJA_SQLITE_PATH"].endswith("mail_archive.sqlite")


# ── Update helpers ────────────────────────────────────────────────────────────

def test_verify_sha256_correct(tmp_path):
    f = tmp_path / "data.bin"
    content = b"hello world"
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert launcher.verify_sha256(f, expected) is True


def test_verify_sha256_wrong(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"hello world")
    assert launcher.verify_sha256(f, "a" * 64) is False


def test_verify_sha256_case_insensitive(tmp_path):
    content = b"mrija"
    f = tmp_path / "data.bin"
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest().upper()
    assert launcher.verify_sha256(f, expected) is True


def test_last_version_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_LAST_VERSION_FILE", tmp_path / ".last_version")
    assert launcher.read_last_version() == ""
    launcher.write_last_version("20260611-120000")
    assert launcher.read_last_version() == "20260611-120000"


def test_fetch_manifest_returns_none_when_no_url(monkeypatch):
    monkeypatch.setattr(launcher, "UPDATE_SERVER_URL", "")
    assert launcher.fetch_manifest() is None


def test_fetch_manifest_returns_none_on_network_error(monkeypatch):
    monkeypatch.setattr(launcher, "UPDATE_SERVER_URL", "http://localhost:19999")

    def bad_urlopen(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", bad_urlopen)
    assert launcher.fetch_manifest() is None


def test_fetch_manifest_parses_json(monkeypatch):
    payload = {"version": "20260611", "filename": "mrija-20260611.sql.gz", "sha256": "a" * 64}
    monkeypatch.setattr(launcher, "UPDATE_SERVER_URL", "http://example.com")

    class FakeResponse:
        def read(self):
            return json.dumps(payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: FakeResponse())

    result = launcher.fetch_manifest()
    assert result == payload


def test_download_file_writes_and_reports_progress(tmp_path, monkeypatch):
    content = b"x" * 1024
    dest = tmp_path / "artifact.sqlite"

    class FakeResponse:
        headers = {"Content-Length": str(len(content))}
        def read(self, n):
            chunk, self._pos = content[self._pos:self._pos + n], self._pos + n
            return chunk
        def __init__(self): self._pos = 0
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: FakeResponse())

    reported = []
    launcher.download_file("http://example.com/file.sqlite", dest, progress=reported.append)

    assert dest.read_bytes() == content
    assert reported[-1] == 100
    assert any(p < 100 for p in reported)  # at least one intermediate value


# ── _Api state transitions ────────────────────────────────────────────────────

class _FakeWindow:
    """Minimal fake pywebview window for testing _Api transitions."""
    def __init__(self):
        self.html_history: list[str] = []
        self.url_history:  list[str] = []
        self.title_history: list[str] = []
        self.js_calls:     list[str] = []

    def load_html(self, html): self.html_history.append(html)
    def load_url(self, url):   self.url_history.append(url)
    def set_title(self, t):    self.title_history.append(t)
    def evaluate_js(self, js): self.js_calls.append(js)


def _make_api(tmp_path, monkeypatch) -> launcher._Api:
    app_dir = tmp_path / "MrijaArchive"
    (app_dir / "web" / "public").mkdir(parents=True)
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    class FakeProc:
        def poll(self): return None
        def terminate(self): pass

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakeProc())
    monkeypatch.setattr(launcher, "wait_for_web", lambda timeout=30: True)

    php = tmp_path / "php.exe"
    php.write_text("php")
    api = launcher._Api(php)
    api._window = _FakeWindow()
    return api


def test_api_stop_archive_shows_stopped_html(tmp_path, monkeypatch):
    api = _make_api(tmp_path, monkeypatch)
    api.stop_archive()
    assert any("Archive stopped" in h for h in api._window.html_history)


def test_api_launch_shows_loading_then_navigates(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    source = app_dir / "data" / "index" / "mail_index.sqlite"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"sqlite")
    output = app_dir / "data" / "client" / "mail_archive.sqlite"

    def fake_run(args, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"client")

    monkeypatch.setattr("subprocess.run", fake_run)

    class FakeProc:
        def poll(self): return None
        def terminate(self): pass

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakeProc())
    monkeypatch.setattr(launcher, "wait_for_web", lambda timeout=30: True)

    php = tmp_path / "php.exe"
    php.write_text("php")
    api = launcher._Api(php)
    api._window = _FakeWindow()

    api._launch()

    assert api._window.url_history  # navigated to WEB_URL
    assert launcher.WEB_URL in api._window.url_history


def test_api_launch_shows_error_when_no_data(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    app_dir.mkdir()
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)
    monkeypatch.setattr(launcher, "wait_for_web", lambda timeout=30: True)

    php = tmp_path / "php.exe"
    api = launcher._Api(php)
    api._window = _FakeWindow()

    api._launch()

    assert any("Error" in t for t in api._window.title_history)
    assert any("No archive data" in h or "error" in h.lower() for h in api._window.html_history)


def test_api_open_file_copies_and_relaunches(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    chosen_file = tmp_path / "my_archive.sqlite"
    chosen_file.write_bytes(b"SQLITE")

    # Mock tkinter at sys.modules level so no _tkinter C extension is needed.
    # `from tkinter import filedialog` reads the attribute off the tkinter mock,
    # so set the return_value on that child mock rather than a separate object.
    fake_tk_mod = MagicMock()
    fake_tk_mod.filedialog.askopenfilename.return_value = str(chosen_file)
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk_mod)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_tk_mod.filedialog)

    monkeypatch.setattr(launcher, "wait_for_web", lambda timeout=30: True)

    source = app_dir / "data" / "index" / "mail_index.sqlite"
    output = app_dir / "data" / "client" / "mail_archive.sqlite"

    def fake_run(args, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"built")

    monkeypatch.setattr("subprocess.run", fake_run)

    class FakeProc:
        def poll(self): return None
        def terminate(self): pass

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakeProc())

    php = tmp_path / "php.exe"
    api = launcher._Api(php)
    api._window = _FakeWindow()

    api.open_file()

    # Chosen file was copied to the index location
    assert source.exists()
    assert source.read_bytes() == b"SQLITE"


def test_api_download_update_skips_when_version_current(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "UPDATE_SERVER_URL", "http://example.com")
    monkeypatch.setattr(launcher, "_LAST_VERSION_FILE", tmp_path / ".last_version")
    launcher.write_last_version("20260611")

    manifest = {
        "version":  "20260611",
        "filename": "mrija-20260611.sqlite",
        "sha256":   "a" * 64,
        "url":      "/updates/mrija-20260611.sqlite",
    }
    monkeypatch.setattr(launcher, "fetch_manifest", lambda: manifest)

    launched = []
    monkeypatch.setattr(launcher, "wait_for_web", lambda timeout=30: True)

    class FakeProc:
        def poll(self): return None
        def terminate(self): pass

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakeProc())

    php = tmp_path / "php.exe"
    api = launcher._Api(php)
    api._window = _FakeWindow()

    # Ensure client DB exists so _launch() succeeds without running subprocess
    app_dir = tmp_path / "MrijaArchive"
    output = app_dir / "data" / "client" / "mail_archive.sqlite"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"db")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    api._do_update()

    # Should have navigated (launched), not shown updating screen
    assert any(launcher.WEB_URL in u for u in api._window.url_history)


def test_api_download_update_bad_checksum_shows_error(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "UPDATE_SERVER_URL", "http://example.com")
    monkeypatch.setattr(launcher, "_LAST_VERSION_FILE", tmp_path / ".last_version")

    content = b"archive data"
    real_sha = hashlib.sha256(content).hexdigest()
    manifest = {
        "version":  "20260612",
        "filename": "mrija.sqlite",
        "sha256":   "b" * 64,  # wrong checksum
        "url":      "http://example.com/updates/mrija.sqlite",
    }
    monkeypatch.setattr(launcher, "fetch_manifest", lambda: manifest)

    def fake_download(url, dest, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        if progress:
            progress(100)

    monkeypatch.setattr(launcher, "download_file", fake_download)

    app_dir = tmp_path / "MrijaArchive"
    app_dir.mkdir()
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    php = tmp_path / "php.exe"
    api = launcher._Api(php)
    api._window = _FakeWindow()

    api._do_update()

    assert any("Checksum" in h or "tampered" in h or "error" in h.lower()
               for h in api._window.html_history)


def test_api_download_update_applies_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "UPDATE_SERVER_URL", "http://example.com")
    monkeypatch.setattr(launcher, "_LAST_VERSION_FILE", tmp_path / ".last_version")

    content = b"new archive"
    real_sha = hashlib.sha256(content).hexdigest()
    manifest = {
        "version":  "20260612",
        "filename": "mrija.sqlite",
        "sha256":   real_sha,
        "url":      "http://example.com/updates/mrija.sqlite",
    }
    monkeypatch.setattr(launcher, "fetch_manifest", lambda: manifest)

    def fake_download(url, dest, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        if progress:
            progress(100)

    monkeypatch.setattr(launcher, "download_file", fake_download)
    monkeypatch.setattr(launcher, "wait_for_web", lambda timeout=30: True)

    app_dir = tmp_path / "MrijaArchive"
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    output = app_dir / "data" / "client" / "mail_archive.sqlite"

    def fake_run(args, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"built")

    monkeypatch.setattr("subprocess.run", fake_run)

    class FakeProc:
        def poll(self): return None
        def terminate(self): pass

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakeProc())

    php = tmp_path / "php.exe"
    api = launcher._Api(php)
    api._window = _FakeWindow()

    api._do_update()

    assert launcher.read_last_version() == "20260612"
    assert any(launcher.WEB_URL in u for u in api._window.url_history)

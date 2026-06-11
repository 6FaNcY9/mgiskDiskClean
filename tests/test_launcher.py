"""
Unit tests for launcher/windows/app.py core functions.
All subprocess and OS calls are mocked so tests run on Linux.
"""
import sys
import os
import shutil
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call
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

    # Create a minimal fake bundle
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
    # Bundle doesn't even need to exist — extraction should be skipped
    monkeypatch.setattr(launcher, "BUNDLE_ZIP", bundle)

    launcher.extract_app_bundle()  # should not raise

    assert compose.read_text() == "existing"  # unchanged


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
    monkeypatch.setattr(launcher, "DATA_SRC", src)  # doesn't exist — would raise if called

    launcher.copy_data()

    assert marker.read_text() == "EXISTING"  # untouched


# ── Docker-free PHP runtime ──────────────────────────────────────────────────

def test_find_php_exe_prefers_bundled_php(tmp_path, monkeypatch):
    app_dir = tmp_path / "MrijaArchive"
    bundled = app_dir / "php" / "php.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("php")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/php")

    assert launcher.find_php_exe() == bundled


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

    proc = launcher.start_php_server(tmp_path / "php.exe", app_dir / "data" / "client" / "mail_archive.sqlite")

    assert isinstance(proc, FakeProc)
    assert "127.0.0.1:8080" in calls[0][0]
    assert calls[0][1]["env"]["MRIJA_SQLITE_PATH"].endswith("mail_archive.sqlite")


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

# Patch winreg before importing — it doesn't exist on Linux
sys.modules.setdefault("winreg", MagicMock())
sys.modules.setdefault("webview", MagicMock())

# Point to launcher source
sys.path.insert(0, str(Path(__file__).parent.parent / "launcher" / "windows"))
import app as launcher


# ── is_docker_installed ────────────────────────────────────────────────────────

def test_docker_installed_via_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr("sys.platform", "linux")
    assert launcher.is_docker_installed() is True


def test_docker_not_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("sys.platform", "linux")
    assert launcher.is_docker_installed() is False


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
    sqlite = app_dir / "data" / "index" / "mail_index.sqlite"
    sqlite.parent.mkdir(parents=True)
    sqlite.write_bytes(b"EXISTING")
    monkeypatch.setattr(launcher, "APP_DIR", app_dir)

    src = tmp_path / "data"
    monkeypatch.setattr(launcher, "DATA_SRC", src)  # doesn't exist — would raise if called

    launcher.copy_data()

    assert sqlite.read_bytes() == b"EXISTING"  # untouched


# ── wait_for_healthy ─────────────────────────────────────────────────────────

def test_wait_for_healthy_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "APP_DIR", tmp_path)
    call_count = [0]

    def _mock_compose(args):
        call_count[0] += 1
        r = MagicMock()
        r.stdout = "healthy" if call_count[0] >= 2 else "starting"
        return r

    monkeypatch.setattr(launcher, "_compose", _mock_compose)
    monkeypatch.setattr("time.sleep", lambda _: None)

    result = launcher.wait_for_healthy(timeout=10)
    assert result is True
    assert call_count[0] == 2


def test_wait_for_healthy_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "APP_DIR", tmp_path)

    def _mock_compose(args):
        r = MagicMock()
        r.stdout = "starting"
        return r

    monkeypatch.setattr(launcher, "_compose", _mock_compose)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("time.monotonic", iter([0, 0.1, 0.2, 100]).__next__)

    result = launcher.wait_for_healthy(timeout=1)
    assert result is False

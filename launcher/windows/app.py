"""MrijaArchive.exe — thin pywebview wrapper around mrija_client FastAPI server."""
from __future__ import annotations
import json
import os
import secrets
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_APPDATA = Path(os.environ.get("APPDATA") or os.environ.get("HOME", "."))
_DATA    = _APPDATA / "MrijaArchive" / "data" / "client"
_PORT    = int(os.environ.get("MRIJA_PORT", "8080"))
_HOST    = "127.0.0.1"
_URL     = f"http://{_HOST}:{_PORT}"
_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
_HEX = "0123456789abcdefABCDEF"

# Development mode: add src/ to sys.path so mrija_client is importable without install
if not getattr(sys, "frozen", False):
    _src = Path(__file__).parent.parent.parent / "src"
    if _src.exists():
        sys.path.insert(0, str(_src))


def _client_db() -> Path:
    return _DATA / "mail_archive.sqlite"


def _bundle_roots() -> list[Path]:
    if getattr(sys, "frozen", False):
        return [Path(sys.executable).resolve().parent]
    return [Path(__file__).resolve().parents[2]]


def _bundled_db_candidates() -> list[Path]:
    hits: list[Path] = []
    for root in _bundle_roots():
        hits.extend([
            root / "data" / "client" / "mail_archive.sqlite",
            root / "data" / "index" / "mail_index.sqlite",
        ])
    return [p for p in hits if p.exists()]


def _install_bundled_db() -> None:
    candidates = _bundled_db_candidates()
    if not candidates:
        return

    source = candidates[0]
    dest = _client_db()
    if dest.exists() and dest.stat().st_mtime >= source.stat().st_mtime:
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    shutil.copy2(source, tmp)
    tmp.replace(dest)


def _find_db() -> Path | None:
    _install_bundled_db()
    if not _DATA.exists():
        return None
    hits = sorted(_DATA.glob("*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def _wait(url: str, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


class _Api:
    """Methods callable from JavaScript via window.pywebview.api.*"""

    def __init__(self, state) -> None:
        self._state = state
        self._win   = None

    def save_attachment(self, sha256: str, filename: str) -> dict:
        if len(sha256) != 64 or not all(c in _HEX for c in sha256):
            return {"error": "Invalid attachment ID."}
        url = f"{_URL}/data/attachment/{sha256}"
        req = urllib.request.Request(
            url,
            headers={"X-Api-Key": os.environ.get("MRIJA_API_KEY", "")},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read(_MAX_ATTACHMENT_BYTES + 1)
                if len(data) > _MAX_ATTACHMENT_BYTES:
                    return {"error": "Attachment is too large to save."}
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 403):
                return {"error": "Attachment not available in this archive."}
            return {"error": f"Download failed (HTTP {exc.code})."}
        except Exception as exc:
            return {"error": f"Download failed: {exc}"}

        downloads = Path(os.environ.get("USERPROFILE", os.environ.get("HOME", "."))) / "Downloads"
        downloads.mkdir(exist_ok=True)
        safe_name = Path(filename).name or "attachment"
        dest = downloads / safe_name
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        i = 1
        while dest.exists():
            dest = downloads / f"{stem} ({i}){suffix}"
            i += 1
        dest.write_bytes(data)
        try:
            os.startfile(str(dest))
        except Exception:
            pass
        return {"ok": True, "path": str(dest)}

    def open_file(self) -> None:
        import webview
        paths = self._win.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("SQLite database (*.sqlite;*.db)", "All files (*.*)"),
        )
        if not paths:
            return
        data = json.dumps({"path": paths[0]}).encode()
        req  = urllib.request.Request(
            f"{_URL}/api/open",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Api-Key":    os.environ.get("MRIJA_API_KEY", ""),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5):
                pass
            if self._win:
                self._win.load_url(_URL)
        except urllib.error.HTTPError as exc:
            if self._win:
                self._win.evaluate_js(f"alert({json.dumps(f'Could not open file (HTTP {exc.code})')})")
        except Exception as exc:
            if self._win:
                self._win.evaluate_js(f"alert({json.dumps(f'Could not open file: {exc}')})")


def main() -> None:
    import webview  # noqa: F401

    if not os.environ.get("MRIJA_API_KEY"):
        os.environ["MRIJA_API_KEY"] = secrets.token_hex(16)

    from mrija_client.state import AppState, ClientState
    from mrija_client.server import create_app
    from mrija_client.db import MailDB

    state   = AppState()
    db_path = _find_db()
    if db_path:
        state.db      = MailDB(db_path)
        state.db_path = db_path
        state.state   = ClientState.RUNNING

    import uvicorn
    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host=_HOST, port=_PORT, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()

    if not _wait(_URL):
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("MrijaArchive", "Server failed to start.")
        root.destroy()
        sys.exit(1)

    api = _Api(state)
    win = webview.create_window(
        "Mrija Archive", url=_URL, width=1280, height=800, resizable=True, js_api=api,
    )
    api._win = win
    webview.start()

    state.state   = ClientState.STOPPED
    server.should_exit = True


if __name__ == "__main__":
    main()

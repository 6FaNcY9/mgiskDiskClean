"""MrijaArchive.exe — thin pywebview wrapper around mrija_client FastAPI server."""
from __future__ import annotations
import json
import os
import secrets
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

# Development mode: add src/ to sys.path so mrija_client is importable without install
if not getattr(sys, "frozen", False):
    _src = Path(__file__).parent.parent.parent / "src"
    if _src.exists():
        sys.path.insert(0, str(_src))


def _find_db() -> Path | None:
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
        if not all(c in "0123456789abcdefABCDEF" for c in sha256):
            return {"error": "Invalid attachment ID."}
        url = f"{_URL}/data/attachment/{sha256}"
        req = urllib.request.Request(
            url,
            headers={"X-Api-Key": os.environ.get("MRIJA_API_KEY", "")},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
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
        except Exception:
            pass


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

"""
MrijaArchive.exe — no-terminal Windows launcher.

Startup sequence:
1. Detect / auto-install Docker Desktop
2. Extract bundled app_bundle.zip to %APPDATA%\\MrijaArchive\\ (first run)
3. Copy sibling data/ folder to app dir (first run)
4. docker compose up -d
5. Wait for MariaDB healthy
6. docker compose run --rm app php web/src/cli/import_archive.php
7. Open pywebview window → localhost:8080
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

# ── Platform guards ───────────────────────────────────────────────────────────
# winreg and CREATE_NO_WINDOW only exist on Windows.
# The module is also imported in Linux tests — guard those symbols.
if sys.platform == "win32":
    import winreg
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
else:
    winreg = None  # type: ignore[assignment]
    _NO_WINDOW = 0

# ── Constants ─────────────────────────────────────────────────────────────────
APPDATA = Path(os.environ.get("APPDATA") or os.environ.get("HOME", "."))
APP_DIR  = APPDATA / "MrijaArchive"
WEB_URL  = "http://localhost:8080"
DOCKER_DOWNLOAD_URL = (
    "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
)

# Locate bundled resources:
# When running as a PyInstaller bundle, sys._MEIPASS is the temp extraction dir.
# When running as plain Python (dev/test), use sibling paths.
_HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
BUNDLE_ZIP = _HERE / "app_bundle.zip"
DATA_SRC   = (
    Path(sys.executable).parent / "data"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent.parent.parent / "data"
)

# Loading screen shown while Docker starts (served from memory, not from file)
_LOADING_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body{background:#111827;display:flex;align-items:center;justify-content:center;
       height:100vh;margin:0;font-family:system-ui,sans-serif}
  .box{text-align:center;color:#818cf8}
  .icon{font-size:3rem;margin-bottom:1rem}
  .title{font-size:1.2rem;font-weight:600;color:#e0e7ff;margin-bottom:.4rem}
  .msg{color:#6366f1;font-size:.85rem}
  .dot{animation:blink 1s infinite}.dot:nth-child(2){animation-delay:.2s}.dot:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:0}40%{opacity:1}}
</style></head>
<body><div class="box">
  <div class="icon">📧</div>
  <div class="title">Mrija Archive</div>
  <div class="msg" id="msg">Starting<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span></div>
</div></body></html>"""

_STOPPED_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body{background:#111827;display:flex;align-items:center;justify-content:center;
       height:100vh;margin:0;font-family:system-ui,sans-serif}
  .box{text-align:center}.icon{font-size:3rem;margin-bottom:1rem}
  .title{font-size:1.1rem;color:#9ca3af;margin-bottom:1rem}
  button{background:#4f46e5;color:#fff;border:none;border-radius:8px;
         padding:.6rem 1.5rem;font-size:.9rem;cursor:pointer}
  button:hover{background:#4338ca}
</style></head>
<body><div class="box">
  <div class="icon">&#9209;</div>
  <div class="title">Archive stopped</div>
  <button onclick="window.pywebview.api.start_archive()">&#9654; Start Again</button>
</div></body></html>"""


# ── Docker detection ──────────────────────────────────────────────────────────

def is_docker_installed() -> bool:
    """Return True if Docker Desktop is installed and docker is on PATH."""
    if sys.platform == "win32" and winreg is not None:
        try:
            winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Docker Inc.\Docker Desktop",
            )
            return True
        except OSError:
            pass
    return shutil.which("docker") is not None


# ── Docker installer download ─────────────────────────────────────────────────

def download_docker_installer(
    progress: Callable[[int], None] | None = None,
) -> Path:
    """Download Docker Desktop installer to APP_DIR. Returns path to installer."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    dest = APP_DIR / "DockerDesktopInstaller.exe"

    def _reporthook(block: int, block_size: int, total: int) -> None:
        if progress and total > 0:
            pct = min(100, int(block * block_size / total * 100))
            progress(pct)

    urllib.request.urlretrieve(DOCKER_DOWNLOAD_URL, dest, _reporthook)
    return dest


def run_docker_installer(installer: Path) -> None:
    """Run Docker Desktop installer silently and wait for it to finish."""
    subprocess.run(
        [str(installer), "install", "--quiet"],
        check=True,
        creationflags=_NO_WINDOW,
    )


# ── App bundle + data ─────────────────────────────────────────────────────────

def extract_app_bundle() -> None:
    """Extract bundled app_bundle.zip to APP_DIR (no-op if already extracted)."""
    if (APP_DIR / "docker-compose.yml").exists():
        return
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE_ZIP) as zf:
        zf.extractall(APP_DIR)


def copy_data() -> None:
    """Copy sibling data/ folder to APP_DIR/data/ (no-op if SQLite already there)."""
    sqlite = APP_DIR / "data" / "index" / "mail_index.sqlite"
    if sqlite.exists():
        return
    if DATA_SRC.exists():
        shutil.copytree(str(DATA_SRC), str(APP_DIR / "data"), dirs_exist_ok=True)


# ── Docker Compose management ─────────────────────────────────────────────────

def _compose(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run `docker compose <args>` from APP_DIR, no terminal window."""
    return subprocess.run(
        ["docker", "compose"] + args,
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )


def start_containers() -> None:
    _compose(["up", "-d"])


def stop_containers() -> None:
    _compose(["stop"])


def wait_for_healthy(timeout: int = 60) -> bool:
    """Poll docker compose ps until MariaDB reports healthy. Returns True on success."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = _compose(["ps", "--format", "{{.Status}}"])
        if "healthy" in r.stdout.lower():
            return True
        time.sleep(2)
    return False


def run_import() -> None:
    """Import SQLite archive into MySQL (idempotent — safe every launch)."""
    _compose(
        [
            "run", "--rm", "app",
            "php", "web/src/cli/import_archive.php",
        ]
    )


# ── pywebview JS API ──────────────────────────────────────────────────────────

class _Api:
    """Methods exposed to JavaScript via window.pywebview.api.*"""

    def __init__(self) -> None:
        self._window = None  # set after webview.create_window

    def stop_archive(self) -> None:
        stop_containers()
        if self._window:
            self._window.load_html(_STOPPED_HTML)
            self._window.set_title("Mrija Archive — Stopped")

    def start_archive(self) -> None:
        if self._window:
            self._window.load_html(_LOADING_HTML)
        start_containers()
        if wait_for_healthy():
            run_import()
            if self._window:
                self._window.load_url(WEB_URL)
                self._window.set_title("Mrija Archive")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import tkinter as tk
    from tkinter import messagebox
    import webview  # imported late so tests can mock it

    # ── 1. Docker detection ───────────────────────────────────────────────
    if not is_docker_installed():
        root = tk.Tk()
        root.withdraw()
        answer = messagebox.askyesno(
            "Docker Desktop Required",
            "Mrija Archive needs Docker Desktop (one-time install, ~600 MB).\n\n"
            "Download and install it now?",
            icon="question",
        )
        root.destroy()
        if not answer:
            sys.exit(0)

        # Show simple progress window
        progress_win = tk.Tk()
        progress_win.title("Installing Docker Desktop")
        progress_win.geometry("420x110")
        progress_win.resizable(False, False)
        progress_win.configure(bg="#111827")
        tk.Label(
            progress_win, text="Downloading Docker Desktop…",
            bg="#111827", fg="#e0e7ff", font=("Segoe UI", 10),
        ).pack(pady=(18, 6))
        from tkinter import ttk
        bar = ttk.Progressbar(progress_win, length=360, mode="determinate")
        bar.pack()
        status_lbl = tk.Label(progress_win, text="0%", bg="#111827", fg="#6b7280",
                               font=("Segoe UI", 8))
        status_lbl.pack(pady=4)

        installer_path: list[Path] = []

        def _do_download() -> None:
            def _progress(pct: int) -> None:
                bar["value"] = pct
                status_lbl.config(text=f"{pct}%")
                progress_win.update_idletasks()
            try:
                p = download_docker_installer(_progress)
                installer_path.append(p)
            finally:
                progress_win.after(0, progress_win.destroy)

        threading.Thread(target=_do_download, daemon=True).start()
        progress_win.mainloop()

        if not installer_path:
            messagebox.showerror("Download Failed", "Could not download Docker Desktop.")
            sys.exit(1)

        run_docker_installer(installer_path[0])

        if not is_docker_installed():
            messagebox.showerror(
                "Installation Incomplete",
                "Docker Desktop installation did not complete.\n"
                "Please restart and try again.",
            )
            sys.exit(1)

    # ── 2. Extract bundle + copy data ────────────────────────────────────
    extract_app_bundle()
    copy_data()

    # ── 3. Create pywebview window (loading screen) ───────────────────────
    api = _Api()
    window = webview.create_window(
        "Mrija Archive",
        html=_LOADING_HTML,
        width=1100,
        height=720,
        resizable=True,
        js_api=api,
    )
    api._window = window

    # ── 4. Start containers in background ────────────────────────────────
    def _startup() -> None:
        start_containers()
        ok = wait_for_healthy(timeout=90)
        if not ok:
            window.load_html(
                '<body style="background:#111827;color:#f87171;font-family:sans-serif;'
                'padding:3rem;text-align:center"><h2>Startup timed out</h2>'
                "<p>Docker containers did not become healthy in 90 seconds.</p></body>"
            )
            window.set_title("Mrija Archive — Error")
            return
        run_import()
        window.load_url(WEB_URL)
        window.set_title("Mrija Archive")

    threading.Thread(target=_startup, daemon=True).start()

    # ── 5. Start webview (blocks until window closed) ─────────────────────
    webview.start()

    # ── 6. Stop containers on exit ────────────────────────────────────────
    stop_containers()


if __name__ == "__main__":
    main()

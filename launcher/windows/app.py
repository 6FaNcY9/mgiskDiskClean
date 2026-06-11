"""
MrijaArchive.exe — no-terminal Windows launcher.

Startup sequence:
1. Extract bundled app_bundle.zip to %APPDATA%\\MrijaArchive\\ (first run)
2. Copy sibling data/ folder to app dir (first run)
3. Build a client SQLite DB from data/index/mail_index.sqlite when needed
4. Start a local PHP server bound to 127.0.0.1
5. Open pywebview window → localhost:8080
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

# ── Platform guards ───────────────────────────────────────────────────────────
# CREATE_NO_WINDOW only exists on Windows.
# The module is also imported in Linux tests — guard that symbol.
if sys.platform == "win32":
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
else:
    _NO_WINDOW = 0

# ── Constants ─────────────────────────────────────────────────────────────────
APPDATA = Path(os.environ.get("APPDATA") or os.environ.get("HOME", "."))
APP_DIR  = APPDATA / "MrijaArchive"
WEB_URL  = "http://localhost:8080"
PHP_PORT = int(os.environ.get("MRIJA_WEB_PORT", "8080"))
WEB_URL = f"http://localhost:{PHP_PORT}"

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
PHP_SRC = (
    Path(sys.executable).parent / "php" / "php.exe"
    if getattr(sys, "frozen", False)
    else Path("php")
)

# Loading screen shown while the local PHP server starts.
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
    data_dir = APP_DIR / "data"
    if data_dir.exists():
        return
    if DATA_SRC.exists():
        shutil.copytree(str(DATA_SRC), str(data_dir), dirs_exist_ok=True)


# ── Docker-free PHP + SQLite runtime ──────────────────────────────────────────

def find_php_exe() -> Path | None:
    """Find a PHP CLI binary for the local no-Docker web server."""
    candidates = [
        APP_DIR / "php" / "php.exe",
        APP_DIR / "php" / "php",
        PHP_SRC,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which("php")
    return Path(found) if found else None


def write_client_config() -> None:
    """Install the SQLite client config as web/config/local.php."""
    src = APP_DIR / "web" / "config" / "local.php.client"
    dest = APP_DIR / "web" / "config" / "local.php"
    if not src.exists():
        raise FileNotFoundError("Missing web/config/local.php.client in app bundle")
    shutil.copyfile(src, dest)


def build_client_database(php: Path) -> Path:
    """Create data/client/mail_archive.sqlite from data/index/mail_index.sqlite."""
    source = APP_DIR / "data" / "index" / "mail_index.sqlite"
    output = APP_DIR / "data" / "client" / "mail_archive.sqlite"
    if output.exists():
        return output
    if not source.exists():
        raise FileNotFoundError(
            "Missing data/index/mail_index.sqlite. Install a client data package first."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(php),
            str(APP_DIR / "web" / "src" / "cli" / "build_client_sqlite.php"),
            "--source", str(source),
            "--output", str(output),
        ],
        check=True,
        cwd=str(APP_DIR),
        creationflags=_NO_WINDOW,
    )
    return output


def start_php_server(php: Path, sqlite_path: Path) -> subprocess.Popen[str]:
    """Start PHP's local web server on localhost only."""
    env = os.environ.copy()
    env["MRIJA_DATA_DIR"] = str(APP_DIR / "data")
    env["MRIJA_SQLITE_PATH"] = str(sqlite_path)
    return subprocess.Popen(
        [
            str(php),
            "-S", f"127.0.0.1:{PHP_PORT}",
            "-t", str(APP_DIR / "web" / "public"),
        ],
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_NO_WINDOW,
    )


def wait_for_web(timeout: int = 30) -> bool:
    """Wait until the local PHP server responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(WEB_URL, timeout=2) as response:
                if 200 <= response.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False

# ── pywebview JS API ──────────────────────────────────────────────────────────

class _Api:
    """Methods exposed to JavaScript via window.pywebview.api.*"""

    def __init__(self) -> None:
        self._window = None  # set after webview.create_window

    def stop_archive(self) -> None:
        if self._window:
            self._window.load_html(_STOPPED_HTML)
            self._window.set_title("Mrija Archive — Stopped")

    def start_archive(self) -> None:
        if self._window:
            self._window.load_html(_LOADING_HTML)
            self._window.load_url(WEB_URL)
            self._window.set_title("Mrija Archive")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import tkinter as tk
    from tkinter import messagebox
    import webview  # imported late so tests can mock it

    # ── 1. Extract bundle + copy data ────────────────────────────────────
    extract_app_bundle()
    copy_data()
    write_client_config()

    php = find_php_exe()
    if php is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "PHP Runtime Missing",
            "Mrija Archive now runs without Docker, but the package must include PHP.\n\n"
            "Rebuild the handoff package with a bundled PHP runtime.",
        )
        root.destroy()
        sys.exit(1)

    try:
        sqlite_path = build_client_database(php)
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Archive Data Missing", str(exc))
        root.destroy()
        sys.exit(1)

    server = start_php_server(php, sqlite_path)

    # ── 2. Create pywebview window (loading screen) ───────────────────────
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

    # ── 3. Wait for local web server in background ───────────────────────
    def _startup() -> None:
        ok = wait_for_web(timeout=30)
        if not ok:
            window.load_html(
                '<body style="background:#111827;color:#f87171;font-family:sans-serif;'
                'padding:3rem;text-align:center"><h2>Startup timed out</h2>'
                "<p>The local web server did not become reachable.</p></body>"
            )
            window.set_title("Mrija Archive — Error")
            return
        window.load_url(WEB_URL)
        window.set_title("Mrija Archive")

    threading.Thread(target=_startup, daemon=True).start()

    # ── 4. Start webview (blocks until window closed) ─────────────────────
    webview.start()

    # ── 5. Stop local server on exit ──────────────────────────────────────
    server.terminate()


if __name__ == "__main__":
    main()

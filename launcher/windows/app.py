"""
MrijaArchive.exe — no-terminal Windows launcher.

Startup sequence:
1. Extract bundled app_bundle.zip to %APPDATA%\\MrijaArchive\\ (first run)
2. Copy sibling data/ folder to app dir (first run)
3. Transition through the state machine:
   no_data  →  (file picker or remote download)
   starting →  build client SQLite, start PHP server
   running  →  show web UI
States: no_data, starting, running, stopped, updating, error
"""
from __future__ import annotations

import hashlib
import json
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
if sys.platform == "win32":
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
else:
    _NO_WINDOW = 0

# ── Constants ─────────────────────────────────────────────────────────────────
APPDATA = Path(os.environ.get("APPDATA") or os.environ.get("HOME", "."))
APP_DIR  = APPDATA / "MrijaArchive"
PHP_PORT = int(os.environ.get("MRIJA_WEB_PORT", "8080"))
WEB_URL  = f"http://127.0.0.1:{PHP_PORT}"

_HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
BUNDLE_ZIP = _HERE / "app_bundle.zip"
DATA_SRC   = (
    Path(sys.executable).parent / "data"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent.parent.parent / "data"
)
PHP_SRC = _HERE / "php" / "php.exe"

UPDATE_SERVER_URL = os.environ.get("UPDATE_SERVER_URL", "http://104.248.242.243").rstrip("/")
_LAST_VERSION_FILE = APP_DIR / "data" / ".last_version"

# ── HTML screens ──────────────────────────────────────────────────────────────

_CSS_BASE = """
<style>
  body{background:#111827;display:flex;align-items:center;justify-content:center;
       height:100vh;margin:0;font-family:system-ui,sans-serif}
  .box{text-align:center;max-width:440px;padding:2rem}
  .icon{font-size:3rem;margin-bottom:1rem}
  .title{font-size:1.2rem;font-weight:600;color:#e0e7ff;margin-bottom:.4rem}
  .sub{color:#6b7280;font-size:.85rem;margin-bottom:1.5rem}
  .msg{color:#6366f1;font-size:.85rem}
  .dot{animation:blink 1s infinite}.dot:nth-child(2){animation-delay:.2s}.dot:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:0}40%{opacity:1}}
  button{background:#4f46e5;color:#fff;border:none;border-radius:8px;
         padding:.6rem 1.4rem;font-size:.88rem;cursor:pointer;margin:.3rem}
  button:hover{background:#4338ca}
  button.sec{background:#1f2937;color:#9ca3af;border:1px solid #374151}
  button.sec:hover{background:#374151}
  .progress-wrap{background:#1f2937;border-radius:6px;height:8px;margin:.8rem 0;overflow:hidden}
  .progress-bar{height:100%;background:#4f46e5;transition:width .3s;border-radius:6px}
  .err{color:#f87171}
</style>
"""

_LOADING_HTML = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{_CSS_BASE}</head>
<body><div class="box">
  <div class="icon">📧</div>
  <div class="title">Mrija Archive</div>
  <div class="msg" id="msg">Starting<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span></div>
</div></body></html>"""

_download_btn = (
    '<button onclick="window.pywebview.api.download_update()">⬇ Download from server</button>'
    if UPDATE_SERVER_URL else ""
)
_NO_DATA_HTML = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{_CSS_BASE}</head>
<body><div class="box">
  <div class="icon">📦</div>
  <div class="title">No archive data</div>
  <div class="sub">Open a local archive file or download the latest data from the server.</div>
  <div>
    <button onclick="window.pywebview.api.open_file()">📂 Open local file&hellip;</button>
    {_download_btn}
  </div>
</div></body></html>"""

_check_update_btn = (
    '<button class="sec" onclick="window.pywebview.api.download_update()">⬇ Check for update</button>'
    if UPDATE_SERVER_URL else ""
)
_STOPPED_HTML = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{_CSS_BASE}</head>
<body><div class="box">
  <div class="icon">⏹</div>
  <div class="title">Archive stopped</div>
  <div>
    <button onclick="window.pywebview.api.start_archive()">▶ Start again</button>
    <button class="sec" onclick="window.pywebview.api.open_file()">📂 Open different file&hellip;</button>
    {_check_update_btn}
  </div>
</div></body></html>"""

def _updating_html(msg: str = "Downloading…", pct: int = 0) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{_CSS_BASE}</head>
<body><div class="box">
  <div class="icon">⬇</div>
  <div class="title">Updating archive</div>
  <div class="progress-wrap"><div class="progress-bar" id="bar" style="width:{pct}%"></div></div>
  <div class="msg" id="pmsg">{msg}</div>
</div></body></html>"""

def _error_html(msg: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{_CSS_BASE}</head>
<body><div class="box">
  <div class="icon">⚠</div>
  <div class="title err">Error</div>
  <div class="sub err">{msg}</div>
  <button class="sec" onclick="window.pywebview.api.open_file()">📂 Open local file&hellip;</button>
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
    """Copy sibling data/ folder to APP_DIR/data/ (no-op if data dir already exists)."""
    data_dir = APP_DIR / "data"
    if data_dir.exists():
        return
    if DATA_SRC.exists():
        shutil.copytree(str(DATA_SRC), str(data_dir), dirs_exist_ok=True)


# ── PHP + SQLite runtime ──────────────────────────────────────────────────────

def _php_env(**extra: str) -> dict:
    """Clean environment for PHP subprocesses.

    PyInstaller injects _MEIPASS into PATH so Python C-extensions find their DLLs.
    PHP inherits this PATH and loads VCRUNTIME140.dll v14.38 (Python's copy) instead
    of the system v14.44 it was compiled against. Replace PATH entirely with
    System32 so PHP always uses the system VC++ runtime.
    """
    sysroot = os.environ.get("SYSTEMROOT", "C:\\Windows")
    env = os.environ.copy()
    env["PATH"] = ";".join([
        sysroot + "\\System32",
        sysroot,
        sysroot + "\\System32\\Wbem",
    ])
    env.update(extra)
    return env


def find_php_exe() -> Path | None:
    """Find a PHP CLI binary. Prefers bundled copy, falls back to PATH."""
    candidates = [APP_DIR / "php" / "php.exe", APP_DIR / "php" / "php", PHP_SRC]
    for c in candidates:
        if c.exists():
            return c
    found = shutil.which("php")
    return Path(found) if found else None


def write_php_ini(php: Path) -> None:
    """Write php.ini next to php.exe to enable the SQLite PDO extension."""
    ini = php.parent / "php.ini"
    ini.write_text(
        "[PHP]\n"
        f"extension_dir={php.parent / 'ext'}\n"
        "extension=pdo_sqlite\n"
        "extension=sqlite3\n"
    )


def write_client_config() -> None:
    """Install the SQLite client config as web/config/local.php."""
    src  = APP_DIR / "web" / "config" / "local.php.client"
    dest = APP_DIR / "web" / "config" / "local.php"
    if not src.exists():
        raise FileNotFoundError("Missing web/config/local.php.client in app bundle")
    shutil.copyfile(src, dest)


def build_client_database(php: Path, source: Path | None = None, force: bool = False) -> Path:
    """Build data/client/mail_archive.sqlite. Uses source or the default index path."""
    if source is None:
        source = APP_DIR / "data" / "index" / "mail_index.sqlite"
    output = APP_DIR / "data" / "client" / "mail_archive.sqlite"
    if output.exists() and not force:
        return output
    if not source.exists():
        raise FileNotFoundError(
            "No archive data found. Open a local .sqlite file or download from server."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(php),
            str(APP_DIR / "web" / "src" / "cli" / "build_client_sqlite.php"),
            "--source", str(source),
            "--output", str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_php_env(),
        cwd=str(APP_DIR),
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no output").strip()
        raise RuntimeError(
            f"build_client_sqlite.php failed (exit {result.returncode}):\n{detail}"
        )
    return output


def start_php_server(php: Path, sqlite_path: Path) -> subprocess.Popen[str]:
    """Start PHP's built-in server bound to 127.0.0.1 only."""
    return subprocess.Popen(
        [str(php), "-S", f"127.0.0.1:{PHP_PORT}", "-t", str(APP_DIR / "web" / "public")],
        cwd=str(APP_DIR),
        env=_php_env(
            MRIJA_DATA_DIR=str(APP_DIR / "data"),
            MRIJA_SQLITE_PATH=str(sqlite_path),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_NO_WINDOW,
    )


def wait_for_web(timeout: int = 30) -> bool:
    """Poll until the local PHP server responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(WEB_URL, timeout=2) as r:
                if 200 <= r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


# ── Update helpers ────────────────────────────────────────────────────────────

def fetch_manifest() -> dict | None:
    """Fetch /updates/manifest.json from UPDATE_SERVER_URL. Returns None on failure."""
    if not UPDATE_SERVER_URL:
        return None
    try:
        req = urllib.request.Request(UPDATE_SERVER_URL + "/updates/manifest.json")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def read_last_version() -> str:
    try:
        return _LAST_VERSION_FILE.read_text().strip()
    except OSError:
        return ""


def write_last_version(version: str) -> None:
    _LAST_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LAST_VERSION_FILE.write_text(version)


def verify_sha256(path: Path, expected: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower()


def download_file(url: str, dest: Path, progress: Callable[[int], None] | None = None) -> None:
    """Download url to dest, calling progress(0-100) periodically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done  = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total > 0:
                progress(min(99, int(done * 100 / total)))
    if progress:
        progress(100)


# ── pywebview JS API ──────────────────────────────────────────────────────────

class _Api:
    """Methods exposed to JavaScript via window.pywebview.api.*"""

    def __init__(self, php: Path) -> None:
        self._php    = php
        self._server: subprocess.Popen[str] | None = None
        self._lock   = threading.Lock()
        self._window = None  # set after webview.create_window

    # ── internal helpers ──────────────────────────────────────────────────

    def _set_html(self, html: str) -> None:
        if self._window:
            self._window.load_html(html)

    def _eval(self, js: str) -> None:
        if self._window:
            self._window.evaluate_js(js)

    def _stop_server(self) -> None:
        with self._lock:
            if self._server and self._server.poll() is None:
                self._server.terminate()
                self._server = None

    def _launch(self, source: Path | None = None, force: bool = False) -> None:
        """Build client DB (if needed), start server, navigate to UI."""
        self._stop_server()
        self._set_html(_LOADING_HTML)
        if self._window:
            self._window.set_title("Mrija Archive — Starting")
        try:
            sqlite_path = build_client_database(self._php, source=source, force=force)
        except Exception as exc:
            self._set_html(_error_html(str(exc)))
            if self._window:
                self._window.set_title("Mrija Archive — Error")
            return
        with self._lock:
            self._server = start_php_server(self._php, sqlite_path)
        ok = wait_for_web(timeout=30)
        if not ok:
            self._set_html(_error_html("The local web server did not respond in time."))
            if self._window:
                self._window.set_title("Mrija Archive — Error")
            return
        if self._window:
            self._window.load_url(WEB_URL)
            self._window.set_title("Mrija Archive")

    # ── JS-callable API ───────────────────────────────────────────────────

    def start_archive(self) -> None:
        threading.Thread(target=self._launch, daemon=True).start()

    def stop_archive(self) -> None:
        self._stop_server()
        self._set_html(_STOPPED_HTML)
        if self._window:
            self._window.set_title("Mrija Archive — Stopped")

    def open_file(self) -> None:
        """Open a file picker and import the chosen .sqlite archive."""
        import sqlite3
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askopenfilename(
            title="Open Mrija Archive",
            filetypes=[("SQLite database", "*.sqlite *.db"), ("All files", "*.*")],
        )
        root.destroy()
        if not chosen:
            return

        chosen_path = Path(chosen)

        # Detect format: archive_emails = already client format; emails = source format
        try:
            with sqlite3.connect(str(chosen_path)) as conn:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
        except Exception:
            tables = set()

        if "archive_emails" in tables:
            # Already in client format — copy directly to output, skip PHP conversion
            dest = APP_DIR / "data" / "client" / "mail_archive.sqlite"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(chosen_path, dest)
            (APP_DIR / "data" / "index" / "mail_index.sqlite").unlink(missing_ok=True)
        else:
            # Source (Python indexer) format — copy and convert via build_client_sqlite.php
            dest = APP_DIR / "data" / "index" / "mail_index.sqlite"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(chosen_path, dest)
            (APP_DIR / "data" / "client" / "mail_archive.sqlite").unlink(missing_ok=True)
        threading.Thread(target=self._launch, daemon=True).start()

    def download_update(self) -> None:
        """Fetch manifest, download artifact, verify SHA-256, rebuild DB."""
        threading.Thread(target=self._do_update, daemon=True).start()

    def _do_update(self) -> None:
        self._set_html(_updating_html("Checking for updates…", 0))
        if self._window:
            self._window.set_title("Mrija Archive — Updating")

        manifest = fetch_manifest()
        if not manifest:
            self._set_html(_error_html("Could not reach the update server. Check your internet connection."))
            if self._window:
                self._window.set_title("Mrija Archive — Error")
            return

        version  = manifest.get("version", "")
        filename = manifest.get("filename", "")
        sha256   = manifest.get("sha256", "")
        url      = manifest.get("url", "")

        if not filename or not sha256 or not url:
            self._set_html(_error_html("Update manifest is missing required fields."))
            return

        if not url.startswith("http"):
            url = UPDATE_SERVER_URL + "/" + url.lstrip("/")

        if version and version == read_last_version():
            # Already up to date — just start
            threading.Thread(target=self._launch, daemon=True).start()
            return

        def _progress(pct: int) -> None:
            self._eval(
                f"document.getElementById('bar') && "
                f"(document.getElementById('bar').style.width='{pct}%');"
                f"document.getElementById('pmsg') && "
                f"(document.getElementById('pmsg').textContent='Downloading… {pct}%');"
            )

        dest = APP_DIR / "data" / "updates" / filename
        try:
            self._set_html(_updating_html("Downloading…", 0))
            download_file(url, dest, _progress)
        except Exception as exc:
            self._set_html(_error_html(f"Download failed: {exc}"))
            return

        self._eval(
            "document.getElementById('pmsg') && "
            "(document.getElementById('pmsg').textContent='Verifying…');"
        )
        if not verify_sha256(dest, sha256):
            dest.unlink(missing_ok=True)
            self._set_html(_error_html("Checksum mismatch — downloaded file is corrupt or tampered."))
            return

        # Copy verified file as the new source index
        index_dest = APP_DIR / "data" / "index" / "mail_index.sqlite"
        index_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dest, index_dest)
        # Remove old client DB so build step runs
        (APP_DIR / "data" / "client" / "mail_archive.sqlite").unlink(missing_ok=True)

        if version:
            write_last_version(version)

        self._launch(force=True)


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
            "Mrija Archive requires a bundled PHP runtime.\n\n"
            "Rebuild the handoff package with PHP included.",
        )
        root.destroy()
        sys.exit(1)
    write_php_ini(php)

    api = _Api(php)

    # ── 2. Create pywebview window ────────────────────────────────────────
    client_db = APP_DIR / "data" / "client" / "mail_archive.sqlite"
    index_db  = APP_DIR / "data" / "index" / "mail_index.sqlite"
    has_data  = client_db.exists() or index_db.exists()

    window = webview.create_window(
        "Mrija Archive",
        html=_LOADING_HTML if has_data else _NO_DATA_HTML,
        width=1100,
        height=720,
        resizable=True,
        js_api=api,
    )
    api._window = window

    # ── 3. Auto-start if data is present ─────────────────────────────────
    def _startup() -> None:
        if has_data:
            api._launch()

    threading.Thread(target=_startup, daemon=True).start()

    # ── 4. Run webview (blocks until window closed) ───────────────────────
    webview.start()

    # ── 5. Cleanup ────────────────────────────────────────────────────────
    api._stop_server()


if __name__ == "__main__":
    main()

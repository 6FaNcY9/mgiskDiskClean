# Mrija Archive Windows Desktop App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single `MrijaArchive.exe` the boss double-clicks to auto-install Docker, start the archive stack, and search emails in a native window — zero terminal, zero config.

**Architecture:** PyInstaller `.exe` bundles the full Docker Compose stack as `app_bundle.zip`. On launch it extracts to `%APPDATA%\MrijaArchive\`, starts Docker containers, imports the SQLite archive into MySQL, then shows a pywebview window serving a PHP search UI on `localhost:8080`. Docker Desktop is auto-downloaded and installed if missing.

**Tech Stack:** Python 3.11, pywebview 5, PyInstaller 6, PHP 8 (built-in server), MariaDB 10.11 (Docker), Docker Desktop, GitHub Actions (Windows build)

---

## File Map

**Create:**
- `web/public/index.php` — full PHP search UI (dark theme, results list + email detail)
- `launcher/windows/app.py` — PyInstaller launcher (Docker detect/install, compose, pywebview)
- `launcher/windows/requirements.txt` — `pywebview>=5`, `pyinstaller>=6`
- `launcher/windows/app.spec` — PyInstaller spec (bundles `app_bundle.zip`, no-console)
- `launcher/windows/build.bat` — Windows build script (pip install + pyinstaller)
- `launcher/windows/package.bat` — creates `MrijaArchive-v1.zip` for sending
- `launcher/windows/package-data-update.sh` — Linux script to export updated SQLite
- `.github/workflows/build-windows-exe.yml` — GitHub Actions Windows build
- `README.txt` — boss-facing one-page instructions

**Modify:**
- `docker-compose.yml` — add `web` service (php -S on port 8080)

---

## Task 1: PHP Email Search UI

**Files:** Create `web/public/index.php`

- [ ] **Step 1: Create `web/public/index.php`**

Write the complete file. It reads DB config from `/app/web/config/local.php` (already exists as `local.php.docker`, baked into the Docker image), connects to MySQL, and renders a single-page search UI.

```php
<?php
/**
 * web/public/index.php — Mrija Archive search UI.
 * Served by: php -S 0.0.0.0:8080 -t /app/web/public
 */
declare(strict_types=1);

// ── DB connection ─────────────────────────────────────────────────────────────
$cfgPath = __DIR__ . '/../config/local.php';
if (!is_file($cfgPath)) {
    die('<p style="color:#f87171;font-family:sans-serif;padding:2rem">Config not found. Ensure local.php exists in web/config/.</p>');
}
/** @var array<string,mixed> $config */
$config = require $cfgPath;
$db = $config['db'] ?? [];
$socket = $db['socket'] ?? '';
if ($socket && file_exists($socket)) {
    $dsn = "mysql:unix_socket=$socket;dbname={$db['dbname']};charset={$db['charset']}";
} else {
    $host = $db['host'] ?? '127.0.0.1';
    $port = $db['port'] ?? 3306;
    $dsn  = "mysql:host=$host;port=$port;dbname={$db['dbname']};charset={$db['charset']}";
}
try {
    $pdo = new PDO($dsn, $db['user'] ?? '', $db['password'] ?? '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (PDOException $e) {
    echo '<!DOCTYPE html><html><body style="background:#111827;color:#f87171;font-family:sans-serif;padding:3rem;text-align:center">';
    echo '<h2>Database starting up…</h2><p>Please wait a moment and refresh the page.</p>';
    echo '<meta http-equiv="refresh" content="3">';
    echo '</body></html>';
    exit;
}

// ── Input ─────────────────────────────────────────────────────────────────────
$q          = trim((string)($_GET['q']      ?? ''));
$mailbox    = trim((string)($_GET['mailbox'] ?? ''));
$selectedId = trim((string)($_GET['id']     ?? ''));
$selMailbox = trim((string)($_GET['smb']    ?? ''));

// ── Data ──────────────────────────────────────────────────────────────────────
$mailboxes = $pdo->query("SELECT DISTINCT mailbox FROM archive_emails ORDER BY mailbox")
                 ->fetchAll(PDO::FETCH_COLUMN);
$total     = (int) $pdo->query("SELECT COUNT(*) FROM archive_emails")->fetchColumn();
$lastImport = $pdo->query(
    "SELECT MAX(imported_at) FROM archive_emails"
)->fetchColumn() ?: '—';

$results = [];
if ($q !== '') {
    $sql    = "SELECT mailbox, stable_id, date, from_addr, subject,
                      LEFT(body_text, 160) AS preview
               FROM archive_emails
               WHERE MATCH(subject, from_addr, to_addrs, cc_addrs, body_text)
                     AGAINST (? IN BOOLEAN MODE)";
    $params = [$q];
    if ($mailbox !== '') {
        $sql    .= " AND mailbox = ?";
        $params[] = $mailbox;
    }
    $sql .= " ORDER BY date DESC LIMIT 50";
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $results = $stmt->fetchAll();
}

$email = null;
if ($selectedId !== '' && $selMailbox !== '') {
    $stmt = $pdo->prepare("SELECT * FROM archive_emails WHERE stable_id = ? AND mailbox = ?");
    $stmt->execute([$selectedId, $selMailbox]);
    $email = $stmt->fetch() ?: null;
    if ($email) {
        $stmt2 = $pdo->prepare("SELECT * FROM archive_attachments WHERE email_stable_id = ? AND mailbox = ?");
        $stmt2->execute([$selectedId, $selMailbox]);
        $email['attachments'] = $stmt2->fetchAll();
    }
}

function esc(string $s): string { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mrija Archive</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#111827;color:#d1d5db;font-family:system-ui,-apple-system,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* Toolbar */
#toolbar{background:#1e1b4b;padding:.5rem 1rem;display:flex;align-items:center;gap:.75rem;border-bottom:1px solid #312e81;flex-shrink:0}
#toolbar .logo{font-size:1.1rem}
#toolbar .title{color:#e0e7ff;font-weight:600;font-size:.9rem}
#search-form{flex:1;display:flex;gap:.5rem;margin:0 1rem}
#q{flex:1;background:#111827;border:1px solid #4f46e5;border-radius:6px;padding:.35rem .75rem;color:#e0e7ff;font-size:.85rem;outline:none}
#q:focus{border-color:#818cf8}
#mb-filter{background:#111827;border:1px solid #374151;color:#9ca3af;border-radius:6px;padding:.35rem .6rem;font-size:.8rem}
#search-btn{background:#4f46e5;color:#fff;border:none;border-radius:6px;padding:.35rem 1rem;font-size:.8rem;cursor:pointer}
#search-btn:hover{background:#4338ca}
.stop-btn{background:transparent;color:#9ca3af;border:1px solid #374151;border-radius:4px;padding:.2rem .6rem;font-size:.7rem;cursor:pointer;margin-left:auto}
.stop-btn:hover{color:#f87171;border-color:#f87171}

/* Main layout */
#main{display:flex;flex:1;overflow:hidden}

/* Results list */
#results{width:42%;border-right:1px solid #1f2937;overflow-y:auto;flex-shrink:0}
.result{padding:.65rem .8rem;border-bottom:1px solid #1f2937;cursor:pointer;border-left:3px solid transparent}
.result:hover{background:#1f2937}
.result.active{background:#1e1b4b;border-left-color:#6366f1}
.result .subj{color:#c7d2fe;font-size:.78rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.result.active .subj{color:#e0e7ff}
.result .meta{color:#6b7280;font-size:.68rem;margin-top:.15rem}
.result .preview{color:#4b5563;font-size:.65rem;margin-top:.2rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.result.active .preview{color:#6b7280}
.result-count{padding:.4rem .8rem;color:#4b5563;font-size:.68rem;text-align:center}
.empty-state{padding:3rem;text-align:center;color:#374151}
.empty-state .icon{font-size:2.5rem;margin-bottom:.75rem}

/* Email detail */
#detail{flex:1;padding:1.2rem;overflow-y:auto}
.detail-header{margin-bottom:1rem;padding-bottom:.75rem;border-bottom:1px solid #1f2937}
.detail-subject{color:#e0e7ff;font-size:1rem;font-weight:600;margin-bottom:.6rem}
.detail-meta{display:grid;grid-template-columns:auto 1fr;gap:.2rem .75rem;font-size:.75rem}
.detail-meta .label{color:#6366f1}
.detail-meta .val{color:#9ca3af}
.detail-body{color:#d1d5db;font-size:.82rem;line-height:1.65;white-space:pre-wrap;word-break:break-word}
.att-list{margin-top:1rem;display:flex;flex-wrap:wrap;gap:.4rem}
.att{display:inline-flex;align-items:center;gap:.35rem;background:#1f2937;border:1px solid #374151;border-radius:4px;padding:.3rem .6rem;font-size:.72rem;color:#9ca3af}

/* Status bar */
#statusbar{background:#111827;border-top:1px solid #1f2937;padding:.25rem 1rem;display:flex;justify-content:space-between;font-size:.65rem;color:#374151;flex-shrink:0}
</style>
</head>
<body>

<div id="toolbar">
  <span class="logo">📧</span>
  <span class="title">Mrija Archive</span>
  <form id="search-form" method="get" action="">
    <input id="q" name="q" type="text" value="<?= esc($q) ?>"
           placeholder="Search emails — subject, from, body…" autofocus>
    <select id="mb-filter" name="mailbox">
      <option value="">All mailboxes</option>
      <?php foreach ($mailboxes as $mb): ?>
        <option value="<?= esc($mb) ?>" <?= $mailbox === $mb ? 'selected' : '' ?>><?= esc($mb) ?></option>
      <?php endforeach ?>
    </select>
    <button id="search-btn" type="submit">Search</button>
  </form>
  <button class="stop-btn" onclick="if(window.pywebview){window.pywebview.api.stop_archive()}else{alert('Use the launcher to stop.')}">■ Stop</button>
</div>

<div id="main">
  <div id="results">
    <?php if ($q === ''): ?>
      <div class="empty-state">
        <div class="icon">🔍</div>
        <div style="color:#6b7280;font-size:.85rem">Search the archive above</div>
        <div style="color:#374151;font-size:.75rem;margin-top:.4rem"><?= number_format($total) ?> emails indexed</div>
      </div>
    <?php elseif (empty($results)): ?>
      <div class="empty-state">
        <div class="icon">📭</div>
        <div style="color:#6b7280;font-size:.85rem">No results for <em><?= esc($q) ?></em></div>
      </div>
    <?php else: ?>
      <?php foreach ($results as $r):
        $isActive = ($r['stable_id'] === $selectedId && $r['mailbox'] === $selMailbox);
        $link = '?' . http_build_query(['q' => $q, 'mailbox' => $mailbox, 'id' => $r['stable_id'], 'smb' => $r['mailbox']]);
      ?>
      <div class="result <?= $isActive ? 'active' : '' ?>" onclick="window.location='<?= esc($link) ?>'">
        <div class="subj"><?= esc($r['subject'] ?: '(no subject)') ?></div>
        <div class="meta"><?= esc($r['from_addr']) ?> · <?= esc(substr($r['date'], 0, 10)) ?> · <em><?= esc($r['mailbox']) ?></em></div>
        <div class="preview"><?= esc($r['preview']) ?></div>
      </div>
      <?php endforeach ?>
      <div class="result-count"><?= count($results) ?> result(s)</div>
    <?php endif ?>
  </div>

  <div id="detail">
    <?php if ($email): ?>
      <div class="detail-header">
        <div class="detail-subject"><?= esc($email['subject'] ?: '(no subject)') ?></div>
        <div class="detail-meta">
          <span class="label">From</span><span class="val"><?= esc($email['from_addr']) ?></span>
          <span class="label">To</span><span class="val"><?= esc($email['to_addrs']) ?></span>
          <?php if ($email['cc_addrs']): ?>
          <span class="label">Cc</span><span class="val"><?= esc($email['cc_addrs']) ?></span>
          <?php endif ?>
          <span class="label">Date</span><span class="val"><?= esc($email['date']) ?></span>
          <span class="label">Mailbox</span><span class="val"><?= esc($email['mailbox']) ?></span>
        </div>
      </div>
      <div class="detail-body"><?= esc($email['body_text']) ?></div>
      <?php if (!empty($email['attachments'])): ?>
        <div class="att-list">
          <?php foreach ($email['attachments'] as $a): ?>
            <span class="att">📎 <?= esc($a['original_filename']) ?></span>
          <?php endforeach ?>
        </div>
      <?php endif ?>
    <?php else: ?>
      <div class="empty-state" style="margin-top:4rem">
        <div style="color:#374151;font-size:.85rem">Select an email to read it</div>
      </div>
    <?php endif ?>
  </div>
</div>

<div id="statusbar">
  <span><?= number_format($total) ?> emails · last import: <?= esc((string)$lastImport) ?></span>
  <span>MariaDB ● PHP ●</span>
</div>

</body>
</html>
```

- [ ] **Step 2: Verify PHP syntax**

```bash
cd /path/to/worktree
php -l web/public/index.php
```

Expected: `No syntax errors detected in web/public/index.php`

- [ ] **Step 3: Commit**

```bash
git add web/public/index.php
git commit -m "feat(web): add PHP email search UI — dark theme, results + detail panel"
```

---

## Task 2: Add Web Service to docker-compose.yml

**Files:** Modify `docker-compose.yml`

- [ ] **Step 1: Add `web` service**

Open `docker-compose.yml`. Add the `web` service block after the `app` service and before `volumes:`:

```yaml
  web:
    build: .
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    environment:
      DB_HOST: db
      DB_PORT: 3306
      DB_NAME: mailreview
      DB_USER: mailreview
      DB_PASS: mailreview
    command: php -S 0.0.0.0:8080 -t /app/web/public
```

- [ ] **Step 2: Verify docker-compose is valid**

```bash
devenv shell -- docker compose config 2>&1 | tail -5
```

Expected: No errors, config prints successfully.

- [ ] **Step 3: Smoke-test web service starts**

```bash
devenv shell -- docker compose up -d web 2>&1 | tail -5
sleep 3
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/
```

Expected HTTP response: `200` (or `500` if DB not connected yet — that's fine, it means PHP is serving).

```bash
devenv shell -- docker compose down
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(docker): add web service — php -S on port 8080 for search UI"
```

---

## Task 3: Python Launcher — Core Functions

**Files:** Create `launcher/windows/app.py`, `launcher/windows/requirements.txt`

- [ ] **Step 1: Create `launcher/windows/requirements.txt`**

```
pywebview>=5.0
pyinstaller>=6.0
```

- [ ] **Step 2: Write `launcher/windows/app.py`**

```python
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
  <div class="icon">⏹</div>
  <div class="title">Archive stopped</div>
  <button onclick="window.pywebview.api.start_archive()">▶ Start Again</button>
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
```

- [ ] **Step 3: Create `launcher/windows/requirements.txt`**

(Already specified in Step 1 — verify the file exists with correct content.)

```
pywebview>=5.0
pyinstaller>=6.0
```

- [ ] **Step 4: Write unit tests for the core functions**

Create `tests/test_launcher.py`:

```python
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
```

- [ ] **Step 5: Run tests on Linux (no Docker needed)**

```bash
cd /path/to/worktree
PYTHONPATH=src python -m pytest tests/test_launcher.py -v
```

Expected: all 8 tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add launcher/windows/app.py launcher/windows/requirements.txt tests/test_launcher.py
git commit -m "feat(launcher): add Windows launcher app.py with Docker detect/install/compose"
```

---

## Task 4: PyInstaller Spec File

**Files:** Create `launcher/windows/app.spec`

This file tells PyInstaller exactly how to bundle the app. It must be created on Windows alongside `build.bat` — but we write it here so it's committed.

- [ ] **Step 1: Create `launcher/windows/app.spec`**

```python
# launcher/windows/app.spec
# PyInstaller spec for MrijaArchive.exe
# Run from launcher/windows/ with: pyinstaller app.spec

import os
from pathlib import Path
block_cipher = None

# Root of repo (two levels up from launcher/windows/)
REPO_ROOT = Path(SPECPATH).parent.parent

# Build the app_bundle.zip at spec-time so it's fresh
import zipfile, shutil, tempfile

bundle_zip = os.path.join(SPECPATH, 'app_bundle.zip')
with zipfile.ZipFile(bundle_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
    for pattern, base in [
        (REPO_ROOT / 'docker-compose.yml',     REPO_ROOT),
        (REPO_ROOT / 'Dockerfile',              REPO_ROOT),
        (REPO_ROOT / 'pyproject.toml',          REPO_ROOT),
    ]:
        if Path(pattern).exists():
            zf.write(str(pattern), str(Path(pattern).relative_to(base)))
    for dirpath, dirnames, filenames in os.walk(str(REPO_ROOT / 'web')):
        # skip config/local.php (secrets) — local.php.docker is included
        for fn in filenames:
            if fn == 'local.php':
                continue
            fpath = os.path.join(dirpath, fn)
            arcname = os.path.relpath(fpath, str(REPO_ROOT))
            zf.write(fpath, arcname)
    for dirpath, dirnames, filenames in os.walk(str(REPO_ROOT / 'src')):
        for fn in filenames:
            fpath = os.path.join(dirpath, fn)
            arcname = os.path.relpath(fpath, str(REPO_ROOT))
            zf.write(fpath, arcname)

a = Analysis(
    ['app.py'],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[
        (bundle_zip, '.'),  # bundled as app_bundle.zip alongside exe in _MEIPASS
    ],
    hiddenimports=['webview', 'clr'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MrijaArchive',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # ← no terminal window
    icon=None,              # add icon path here if you have one
)
```

- [ ] **Step 2: Commit**

```bash
git add launcher/windows/app.spec
git commit -m "build(launcher): add PyInstaller spec for MrijaArchive.exe"
```

---

## Task 5: Build & Package Scripts

**Files:** Create `launcher/windows/build.bat`, `launcher/windows/package.bat`, `launcher/windows/package-data-update.sh`, `README.txt`

- [ ] **Step 1: Create `launcher/windows/build.bat`**

```batch
@echo off
:: launcher/windows/build.bat
:: Run on Windows (or GitHub Actions windows-latest) to build MrijaArchive.exe
:: Usage: double-click or run from launcher\windows\

echo === MrijaArchive Windows Build ===

:: Install Python deps
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (echo ERROR: pip install failed & exit /b 1)

:: Build exe
pyinstaller app.spec --noconfirm
if %ERRORLEVEL% neq 0 (echo ERROR: pyinstaller failed & exit /b 1)

echo.
echo Build complete: dist\MrijaArchive.exe
echo Run package.bat to create the zip for the boss.
```

- [ ] **Step 2: Create `launcher/windows/package.bat`**

```batch
@echo off
:: launcher/windows/package.bat
:: Creates MrijaArchive-v1.zip for sending to the boss.
:: Run from launcher\windows\ after build.bat succeeds.
:: Expects: ..\..\data\index\mail_index.sqlite to exist (populated by sync-all).

set OUT=..\..\MrijaArchive-v1.zip
set EXE=dist\MrijaArchive.exe
set DATA=..\..\data\index\mail_index.sqlite
set README=..\..\README.txt

if not exist "%EXE%" (
    echo ERROR: %EXE% not found. Run build.bat first.
    exit /b 1
)
if not exist "%DATA%" (
    echo ERROR: %DATA% not found. Run sync-all on Linux first to populate SQLite.
    exit /b 1
)

:: Build zip using PowerShell (available on all modern Windows)
powershell -Command ^
  "$files = @('%EXE%', '%README%'); ^
   $zip = '%OUT%'; ^
   if (Test-Path $zip) { Remove-Item $zip }; ^
   Add-Type -Assembly System.IO.Compression.FileSystem; ^
   $archive = [System.IO.Compression.ZipFile]::Open($zip, 'Create'); ^
   foreach ($f in $files) { ^
     [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $f, [System.IO.Path]::GetFileName($f)) ^
   }; ^
   $dataDir = '%DATA%'; ^
   [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $dataDir, 'data\index\mail_index.sqlite'); ^
   $archive.Dispose()"

if %ERRORLEVEL% neq 0 (echo ERROR: zip creation failed & exit /b 1)
echo.
echo Package ready: %OUT%
echo Send this zip to the boss.
```

- [ ] **Step 3: Create `launcher/windows/package-data-update.sh`**

Run this on Linux to export a fresh SQLite for the boss (data-only update):

```bash
#!/usr/bin/env bash
# launcher/windows/package-data-update.sh
# Export updated mail_index.sqlite as a small zip for the boss.
# Run on Linux after sync-all has refreshed the data.
# Usage: bash launcher/windows/package-data-update.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SQLITE="$REPO_ROOT/data/index/mail_index.sqlite"
OUT="$REPO_ROOT/MrijaArchive-data-update.zip"

if [ ! -f "$SQLITE" ]; then
    echo "ERROR: $SQLITE not found. Run sync-all first."
    exit 1
fi

rm -f "$OUT"
zip -j "$OUT" "$SQLITE"
echo "Data update package: $OUT"
echo "Send to boss → they drop mail_index.sqlite into:"
echo "  %APPDATA%\\MrijaArchive\\data\\index\\"
echo "Then click Stop + Start in the app to reimport."
```

```bash
chmod +x launcher/windows/package-data-update.sh
```

- [ ] **Step 4: Create `README.txt`**

```
Mrija Archive
=============

This app lets you search the mrija.org email archive.

HOW TO START
------------
1. Double-click MrijaArchive.exe

   If Docker Desktop is not installed, the app will download and
   install it automatically (one-time, ~600 MB, takes 5–10 minutes).

2. The app will start and show the email search interface.
   This takes about 30 seconds on first launch.

HOW TO SEARCH
-------------
Type any keyword in the search box and press Enter or click Search.
You can filter by mailbox using the dropdown.
Click any result to read the full email.

HOW TO STOP
-----------
Click the "■ Stop" button in the top-right corner.
Close the window normally — the app will stop automatically.

GETTING NEW EMAILS
------------------
When a data update zip is sent to you (MrijaArchive-data-update.zip):
1. Open the zip and copy mail_index.sqlite to:
   %APPDATA%\MrijaArchive\data\index\
   (paste that path into Windows Explorer address bar)
2. Click ■ Stop then ▶ Start Again in the app.

SYSTEM REQUIREMENTS
-------------------
- Windows 10 or 11
- Internet connection (first launch only, for Docker Desktop)
- ~2 GB disk space
```

- [ ] **Step 5: Verify bash syntax of the shell script**

```bash
bash -n launcher/windows/package-data-update.sh && echo "syntax ok"
```

- [ ] **Step 6: Commit**

```bash
git add launcher/windows/build.bat launcher/windows/package.bat \
        launcher/windows/package-data-update.sh README.txt
git commit -m "build(launcher): add build/package scripts and boss README"
```

---

## Task 6: GitHub Actions Windows Build

**Files:** Create `.github/workflows/build-windows-exe.yml`

This lets you build `MrijaArchive.exe` from any machine by pushing to GitHub — no Windows PC required.

- [ ] **Step 1: Create `.github/workflows/build-windows-exe.yml`**

```yaml
# .github/workflows/build-windows-exe.yml
# Builds MrijaArchive.exe on a Windows runner and uploads it as an artifact.
# Trigger: push to feature/mailbox-archive, or manually via workflow_dispatch.
# Download the .exe from: Actions tab → latest run → Artifacts → MrijaArchive-exe

name: Build Windows EXE

on:
  push:
    branches: [feature/mailbox-archive]
    paths:
      - 'launcher/windows/**'
      - 'web/**'
      - 'src/**'
      - 'docker-compose.yml'
      - 'Dockerfile'
  workflow_dispatch:

jobs:
  build:
    runs-on: windows-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        working-directory: launcher/windows
        run: pip install -r requirements.txt

      - name: Build exe
        working-directory: launcher/windows
        run: pyinstaller app.spec --noconfirm

      - name: Upload exe artifact
        uses: actions/upload-artifact@v4
        with:
          name: MrijaArchive-exe
          path: launcher/windows/dist/MrijaArchive.exe
          retention-days: 30
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/build-windows-exe.yml
git commit -m "ci: add GitHub Actions workflow to build MrijaArchive.exe on Windows"
```

---

## Task 7: Final Integration Test

**Files:** No new files — verify everything works end-to-end.

- [ ] **Step 1: Run the full pytest suite — must all pass**

```bash
cd /path/to/worktree
devenv shell -- python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests PASSED (253 existing + 8 new launcher tests = 261 total).

- [ ] **Step 2: Smoke-test the web UI end-to-end**

```bash
devenv shell -- docker compose up -d 2>&1 | tail -5
sleep 15
# Run migrations and import
devenv shell -- docker compose exec web php web/src/cli/migrate.php
devenv shell -- docker compose exec web php web/src/cli/import_archive.php

# Check the search UI responds
curl -s "http://localhost:8080/?q=fixture_unique_keyword_alpha" | grep -c "result"
```

Expected: at least `1` result printed.

- [ ] **Step 3: Verify PHP syntax of index.php**

```bash
php -l web/public/index.php
```

Expected: `No syntax errors detected`

- [ ] **Step 4: Verify the data-update script runs**

```bash
bash launcher/windows/package-data-update.sh 2>&1
```

Expected: prints the path to `MrijaArchive-data-update.zip` (or an error if SQLite not present — that's fine for now).

- [ ] **Step 5: Final commit**

```bash
git add -A
git status
# If there are any untracked changes (unlikely), stage and commit
git commit -m "chore: final integration test — 261 tests passing, web UI smoke-tested" \
  --allow-empty
```

---

## Self-Review

**Spec coverage:**
- ✅ PHP search UI with dark theme, results list, email detail, attachment badges → Task 1
- ✅ docker-compose.yml web service on port 8080 → Task 2
- ✅ Docker detection (registry + PATH) → Task 3 `is_docker_installed()`
- ✅ Docker auto-download + install → Task 3 `download_docker_installer()` + `run_docker_installer()`
- ✅ First-run bundle extraction → Task 3 `extract_app_bundle()`
- ✅ First-run data copy → Task 3 `copy_data()`
- ✅ `docker compose up -d` + health wait → Task 3 `start_containers()` + `wait_for_healthy()`
- ✅ MySQL import on every launch → Task 3 `run_import()`
- ✅ pywebview window loading screen → Task 3 `_LOADING_HTML`
- ✅ pywebview window → localhost:8080 → Task 3 `main()`
- ✅ Stop button via JS API → Task 3 `_Api.stop_archive()`
- ✅ Reload / start again after stop → Task 3 `_Api.start_archive()`
- ✅ No terminal (noconsole) → Task 4 `app.spec`
- ✅ PyInstaller spec bundles app_bundle.zip → Task 4
- ✅ build.bat / package.bat → Task 5
- ✅ Data update package script → Task 5
- ✅ Boss README.txt → Task 5
- ✅ GitHub Actions Windows build → Task 6

**Type consistency:**
- `_compose()` used in `start_containers`, `stop_containers`, `wait_for_healthy`, `run_import` — consistent throughout
- `APP_DIR`, `BUNDLE_ZIP`, `DATA_SRC` defined once, used in tests via `monkeypatch.setattr(launcher, ...)` — consistent
- `_Api.stop_archive()` called from PHP via `window.pywebview.api.stop_archive()` — matches
- `_Api.start_archive()` called from `_STOPPED_HTML` button via `window.pywebview.api.start_archive()` — matches

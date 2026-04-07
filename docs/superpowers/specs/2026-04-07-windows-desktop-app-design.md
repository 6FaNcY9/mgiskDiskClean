# Mrija Archive — Windows Desktop App Design

**Date:** 2026-04-07
**Goal:** A self-contained Windows desktop app that lets the boss search the mrija.org email archive with zero technical knowledge. No terminal, no browser, no configuration.

---

## Overview

The app ships as a single `.exe` built with PyInstaller. The boss double-clicks it, Docker installs automatically if missing, containers start in the background, and a native window opens showing the email search interface — all without a terminal ever appearing.

Data is distributed as a ZIP file (containing the `.exe` + a `mail_index.sqlite` export). When new emails need to be added, the developer exports a fresh SQLite and sends an update ZIP.

---

## Architecture

```
MrijaArchive.exe  (PyInstaller — no Python required)
  │
  ├── Docker detection & auto-install
  │     └── downloads Docker Desktop installer if not found
  │
  ├── App file extraction  (first run only)
  │     └── extracts bundled app.zip → %APPDATA%\MrijaArchive\
  │         ├── docker-compose.yml
  │         ├── web/  (PHP search UI + migrate.php + migrations)
  │         └── Dockerfile
  │
  ├── Data copy  (first run only)
  │     └── copies data/ folder (SQLite) into %APPDATA%\MrijaArchive\data\
  │
  ├── Docker Compose  (every launch)
  │     └── docker compose up -d  →  waits for MariaDB health
  │
  ├── Archive import  (every launch, idempotent)
  │     └── docker compose run --rm app php web/src/cli/import_archive.php
  │
  └── pywebview window
        └── shows localhost:8080  (PHP search UI)
```

---

## Deliverables

### 1. `launcher/windows/app.py`
Python app (compiled to `.exe` via PyInstaller). Responsibilities:
- **Docker detection:** check `HKLM\SOFTWARE\Docker Inc.\Docker Desktop` registry key + `docker` in PATH
- **Auto-install:** if Docker missing → show dialog → download `Docker Desktop Installer.exe` from `https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe` → run installer → wait for completion → retry docker check
- **First-run extraction:** if `%APPDATA%\MrijaArchive\` doesn't exist → extract bundled `app.zip` (PyInstaller data file) into that directory
- **Data copy:** if no SQLite at `%APPDATA%\MrijaArchive\data\index\mail_index.sqlite` → copy from exe's sibling `data/` folder
- **Container startup:** `docker compose up -d` → poll `docker compose ps` until MariaDB healthy (30s timeout with progress indicator)
- **Import:** `docker compose run --rm app php web/src/cli/import_archive.php` (idempotent — safe every launch)
- **Window:** open pywebview window (`localhost:8080`, title "Mrija Archive", 1100×700, resizable). Window toolbar has: status dot + "■ Stop" button.
- **Stop:** "■ Stop" runs `docker compose stop`, updates status dot to grey, disables webview

Window states:
- `installing_docker` → progress dialog with download bar
- `starting` → loading screen ("Starting archive…")
- `running` → full pywebview showing PHP UI
- `stopped` → dimmed webview + "▶ Start" button
- `error` → red status + error message + retry button

### 2. `web/public/index.php`
PHP search UI served by the Docker `app` container on port 8080. Single-file PHP app:
- Full-page dark theme (matching mockup: indigo/slate palette)
- Search form: text input + mailbox dropdown (populated from `SELECT DISTINCT mailbox FROM archive_emails`) + Search button
- Results list (left panel): subject, from, date, body preview (150 chars)
- Email detail (right panel): full headers + body + attachment badges
- On search: `MATCH(...) AGAINST(? IN BOOLEAN MODE)` query, limit 50
- Status bar: total email count + import timestamp
- No JavaScript framework — vanilla JS for panel interaction only

### 3. `docker-compose.yml` update
Add `web` service to expose the PHP search UI on port 8080:
```yaml
  web:
    build: .
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

### 4. `launcher/windows/build.bat`
**Must run on Windows** — PyInstaller cannot cross-compile from Linux. Run this on any Windows machine (your own PC, a VM, or a GitHub Actions `windows-latest` runner). Uses `pip install pyinstaller pywebview` then `pyinstaller app.spec` → produces `dist/MrijaArchive.exe`. Bundles `app.zip` (Docker Compose + web files) as a PyInstaller data file.

A `launcher/windows/build-github-actions.yml` workflow is also provided so the `.exe` can be built automatically via GitHub Actions on push, with the artifact downloadable from the Actions tab — no Windows machine needed.

### 5. `launcher/windows/package.bat`
Creates `MrijaArchive-v1.zip` for sending to boss (run on Windows after build):
```
MrijaArchive-v1.zip
├── MrijaArchive.exe
├── data/
│   └── index/
│       └── mail_index.sqlite    ← copied from data/index/ at package time
└── README.txt
```

---

## Data Update Workflow

When the developer has new emails to send:
```bash
# On Linux: export fresh SQLite, create update zip
bash launcher/windows/package-data-update.sh
# → produces MrijaArchive-data-update.zip containing only mail_index.sqlite
```

Boss drops `mail_index.sqlite` into `%APPDATA%\MrijaArchive\data\index\`, then clicks "Reload Data" button in the app (triggers re-import).

---

## File Map

**Create:**
- `launcher/windows/app.py` — PyInstaller launcher
- `launcher/windows/app.spec` — PyInstaller spec file
- `launcher/windows/build.bat` — build script (run on Windows)
- `launcher/windows/package.bat` — ZIP packager (run on Windows)
- `launcher/windows/package-data-update.bat` — data-only update packager
- `launcher/windows/requirements.txt` — pywebview, pyinstaller
- `.github/workflows/build-windows-exe.yml` — GitHub Actions build (optional)
- `web/public/index.php` — PHP email search UI
- `README.txt` — boss-facing instructions

**Modify:**
- `docker-compose.yml` — add `web` service on port 8080

---

## Constraints

- **No terminal ever visible** — PyInstaller `--noconsole`, all subprocess calls use `CREATE_NO_WINDOW`
- **No Python on boss's PC** — PyInstaller bundles the interpreter
- **Windows 10/11 only** — WebView2 is built in; no extra install
- **Docker Desktop required** — auto-installed on first run if absent
- **Data is read-only** — no delete, no send, search only
- **Build runs on Windows** — PyInstaller must run on the target platform; use your own Windows PC, a VM, or GitHub Actions `windows-latest` runner

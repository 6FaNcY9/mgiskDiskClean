# MrijaArchive Client Redesign

**Date:** 2026-06-13
**Branch:** feature/docker-free-windows-client → new feature branch per sub-project
**Status:** Design approved, pending implementation plan

---

## Goal

Replace the current PHP-based Windows-only client with a cross-platform Python client:
- Linux: terminal TUI (Rich) + browser-based search UI
- Windows: single exe with embedded pywebview window (same UX as today)
- No Docker, no PHP, no bundled php.exe
- FastAPI backend serves both the search UI (HTMX) and a JSON control API
- Control API documented via OpenAPI → Postman collection for automated testing
- Clear migration path to Rust (Axum): replace FastAPI, keep the HTMX frontend unchanged

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Linux (dev)              │  Windows (end user)              │
│                           │                                  │
│  python -m mrija_client   │  MrijaArchive.exe                │
│       │                   │       │                          │
│    Rich TUI               │  pywebview window                │
│    (status, progress)     │  (wraps localhost:8080)          │
│       │                   │       │                          │
│       └──────── FastAPI server ───┘                          │
│                  ├── /           static HTMX frontend        │
│                  ├── /data/*     search, browse, attachment  │
│                  └── /api/*      control (JSON, authed)      │
│                        │                                     │
│                   SQLite DB  ←── DO droplet (push-sqlite.sh) │
└─────────────────────────────────────────────────────────────┘
```

---

## Repo Changes

### Removed

| Path | Reason |
|---|---|
| `web/` | PHP UI replaced by HTMX static frontend |
| `docker/`, `docker-compose.yml`, `.env.example` | Docker-free going forward |
| `launcher/windows/app.py` (current 700-line version) | Replaced by thin wrapper |
| `src/tui/` | Experimental, superseded by new Rich TUI |
| `conductor/tracks/admin_client_windows_20260605/` | Completed track, stale |
| `conductor/tracks/ui_redesign_20260604/` | Docker-era, superseded |

### Kept Unchanged

- `src/maildir_report/` — core email sync and indexing pipeline
- `tests/` — existing pytest suite (updated where needed)
- `scripts/push-sqlite.sh` — DO droplet update distribution
- `conductor/` product docs and tech-stack guides (useful)
- `data/` — local email archive, gitignored, never touched by cleanup

### New Layout

```
mrijaPageClean/
├── src/
│   ├── maildir_report/          ← unchanged
│   └── mrija_client/            ← NEW cross-platform client
│       ├── __init__.py
│       ├── server.py            ← FastAPI app (mounts /data and /api routers)
│       ├── api/
│       │   ├── __init__.py
│       │   ├── control.py       ← /api/* endpoints (JSON, X-Api-Key auth)
│       │   └── data.py          ← /data/* endpoints (HTML fragments for HTMX)
│       ├── static/
│       │   ├── index.html       ← single-page shell
│       │   ├── htmx.min.js      ← bundled locally, no CDN
│       │   └── style.css        ← ported from current web/public/
│       ├── templates/           ← Jinja2 HTML fragments
│       │   ├── search_results.html
│       │   ├── email_detail.html
│       │   └── browse.html
│       ├── updater.py           ← download + verify + apply SQLite from DO
│       └── tui.py               ← Rich TUI (Linux entry point)
├── launcher/
│   └── windows/
│       ├── app.py               ← ~30-line pywebview wrapper
│       ├── app.spec             ← PyInstaller spec
│       └── build.bat
├── tests/
│   ├── test_launcher.py         ← updated for new thin wrapper
│   ├── test_client_api.py       ← NEW: control API tests
│   └── ...                     ← existing pipeline tests unchanged
├── scripts/
│   └── push-sqlite.sh
└── docs/
    └── superpowers/specs/
        └── 2026-06-13-mrija-client-redesign-design.md
```

---

## FastAPI Server (`server.py`)

Single FastAPI app with two router groups:

### `/data/*` — HTML fragments (HTMX, no auth required)

| Route | Returns |
|---|---|
| `GET /data/search?q=&page=` | `<ul>` of matching email rows |
| `GET /data/email/{id}` | Email detail HTML fragment |
| `GET /data/browse?mailbox=&page=` | Browse-by-mailbox fragment |
| `GET /data/attachment/{sha256}` | File download (redirect to data dir) |

All routes use Jinja2 templates. SQL queries are ported directly from the current PHP — same SQLite schema (`archive_emails`, `archive_attachments`).

Search uses 300ms HTMX debounce: `hx-get="/data/search" hx-trigger="keyup changed delay:300ms"`.

### `/api/*` — JSON control (requires `X-Api-Key` header)

| Route | Description |
|---|---|
| `GET /api/status` | `{state, email_count, attachment_count, last_updated, version, db_path}` |
| `POST /api/update` | Trigger background download from DO droplet; returns `{job_id}` |
| `GET /api/update/progress` | SSE stream: `{percent, bytes_downloaded, status}` |
| `POST /api/open` | `{path}` — load a local `.sqlite` or `.sqlite.gz` as active DB |
| `POST /api/restart` | Hot-reload: close DB, re-open, return to running state |
| `POST /api/shutdown` | Graceful process shutdown |

`MRIJA_API_KEY` env var sets the key. If unset, defaults to a random key printed to stdout on startup (dev convenience).

### `/openapi.json`

Auto-generated by FastAPI. Used to sync the Postman collection.

---

## HTMX Frontend (`static/`)

No JavaScript framework. Three files:

- `index.html` — shell with search input, `#results` swap target, status bar
- `htmx.min.js` — local copy (no CDN dependency, works offline)
- `style.css` — ported from current `web/public/` CSS

The page loads once. HTMX handles all dynamic content via HTML fragment swaps. No JSON parsing in the browser, no client-side state.

---

## Linux TUI (`tui.py`, Rich)

```
┌─ MrijaArchive ──────────────────────────┐
│ State:   running                        │
│ Emails:  29,402   Attachments: 20,485   │
│ Updated: 2026-06-13T00:45Z              │
├─────────────────────────────────────────┤
│ Downloading update...  [████░░░░] 47%   │
├─────────────────────────────────────────┤
│ Server: http://localhost:8080           │
│ [q] quit   [u] update   [o] open file   │
└─────────────────────────────────────────┘
```

Startup sequence:
1. Start FastAPI via `uvicorn` in a subprocess
2. Poll `/api/status` until state = `running`
3. Open browser automatically (`webbrowser.open`)
4. Subscribe to `/api/update/progress` SSE for progress bar updates
5. Keyboard: `q` → shutdown, `u` → trigger update, `o` → file picker

---

## Windows Launcher (`launcher/windows/app.py`)

Thin wrapper only — ~30 lines:

1. Start FastAPI server in a background thread (same `uvicorn` call)
2. Wait until server responds on localhost:8080
3. Open `pywebview.create_window("MrijaArchive", "http://localhost:8080")`
4. On window close → send `POST /api/shutdown`

PyInstaller bundles: Python runtime + uvicorn + `src/mrija_client/` + `static/` + `templates/`. No PHP, no `app_bundle.zip`, no extraction on first run. Exe works immediately on double-click.

---

## Postman Collection

Two environments:

| Environment | `base_url` | `api_key` |
|---|---|---|
| `local` | `http://localhost:8080` | `dev-key` |
| `do-relay` | `http://104.248.242.243` | `<prod-key>` |

Test scenarios:
1. **Status check** — GET /api/status returns valid JSON with expected fields
2. **Update flow** — POST /api/update → poll /api/update/progress → verify state returns to `running`
3. **SHA256 verification** — downloaded archive SHA256 matches manifest
4. **Open local file** — POST /api/open with valid path → status shows new email count
5. **Auth rejection** — requests without `X-Api-Key` return 401

Collection synced from `/openapi.json` via Postman's spec import. Tests written in Postman's scripting layer (pre-request + test scripts).

---

## DO Remote Management

The DO droplet calls the client's `/api/*` endpoints directly over HTTP. No new protocol.

Client binding:
- `--bind 127.0.0.1` (default) — local-only, DO cannot reach it
- `--bind 0.0.0.0` — exposed on all interfaces, DO can call it by client IP

The DO droplet can trigger an update on any registered client:
```bash
curl -X POST http://<client-ip>:8080/api/update \
  -H "X-Api-Key: <key>"
```

Client IP registration (future): clients POST their IP + key to a `/register` endpoint on the DO droplet on startup. The droplet maintains a registry. This is out of scope for the initial implementation.

---

## Rust Migration Path

The HTMX frontend is plain HTML — it will not change during a Rust migration. The migration replaces only the FastAPI server with an Axum server implementing the same routes. Steps when ready:

1. Export `/openapi.json` from the running FastAPI app
2. Implement the same routes in Axum using the OpenAPI spec as contract
3. Run the Postman collection against the Axum server to verify parity
4. Replace the PyInstaller bundle: swap uvicorn for the compiled Axum binary

---

## Sub-project Order

1. **Repo cleanup** — remove Docker/PHP artifacts, restructure directories
2. **FastAPI server + HTMX frontend** — port PHP UI, implement /data/* routes
3. **Control API** — /api/* endpoints, auth, OpenAPI
4. **Linux TUI** — Rich terminal wrapper
5. **Postman collection** — test suite against control API
6. **Windows exe rebuild** — thin pywebview wrapper + PyInstaller spec
7. **DO remote management** — expose bind option, test remote trigger

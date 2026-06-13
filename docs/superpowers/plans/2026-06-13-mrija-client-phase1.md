# MrijaArchive Client Phase 1 — Linux Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the PHP+Docker stack with a cross-platform Python client: FastAPI serves a search UI (HTMX static frontend) and a JSON control API; a Rich TUI wraps the process on Linux.

**Architecture:** Single FastAPI app with two route groups — `/data/*` returns HTML fragments for HTMX, `/api/*` returns JSON and requires `X-Api-Key`. A Rich Live TUI launches the server, opens the browser, and streams status. The Windows exe (Phase 2) is a thin pywebview wrapper around the same server.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, Jinja2, htmx 2.0, Rich, pytest, httpx (TestClient)

---

## File Map

```
src/mrija_client/
  __init__.py          package marker
  __main__.py          entry point: python -m mrija_client
  server.py            FastAPI app factory: create_app(state) -> FastAPI
  state.py             AppState dataclass + ClientState enum (shared mutable state)
  db.py                MailDB: SQLite queries (search, browse, email detail, attachment lookup)
  updater.py           download manifest, fetch archive, verify SHA256, decompress, swap DB
  api/
    __init__.py
    control.py         /api/* routes (status, update, open, restart, shutdown)
    data.py            /data/* routes (search, browse, email, attachment download)
  static/
    index.html         single-page shell (search input + swap targets)
    htmx.min.js        bundled locally — no CDN
    style.css          minimal dark-mode CSS
  templates/
    search_results.html
    email_detail.html
    browse.html

tests/
  test_db.py           MailDB unit tests (fixture SQLite)
  test_data_routes.py  /data/* route tests via TestClient
  test_control_api.py  /api/* route tests via TestClient
  test_updater.py      updater unit tests (mock urllib)
```

**SQLite schema (read-only — already built by push-sqlite.sh):**
```sql
archive_emails(mailbox, stable_id, filepath, folder, date,
               from_addr, to_addrs, cc_addrs, subject, body_text,
               total_size_bytes, imported_at)
archive_attachments(mailbox, email_stable_id, stored_path, sha256,
                    size, mime, original_filename, imported_at)
```
Primary keys: `(mailbox, stable_id)` for emails, `(mailbox, email_stable_id, sha256)` for attachments.

---

## Task 1: Repo cleanup

**Files:** Delete only — no new files.

- [ ] **Step 1: Remove Docker and PHP artifacts**

```bash
git rm -r web/ docker/ docker-compose.yml .env.example 2>/dev/null || true
git rm -r src/tui/ 2>/dev/null || true
```

- [ ] **Step 2: Remove stale conductor tracks**

```bash
git rm -r conductor/tracks/admin_client_windows_20260605/ \
          conductor/tracks/ui_redesign_20260604/ 2>/dev/null || true
```

- [ ] **Step 3: Remove old Windows launcher (will be replaced in Phase 2)**

```bash
git rm launcher/windows/app.py 2>/dev/null || true
```

- [ ] **Step 4: Verify nothing critical was removed**

```bash
git status
# Confirm src/maildir_report/, tests/, scripts/push-sqlite.sh still present
ls src/maildir_report/ tests/ scripts/push-sqlite.sh
```

- [ ] **Step 5: Commit cleanup**

```bash
git commit -m "chore: remove Docker/PHP/TUI artifacts — replaced by Python client"
```

---

## Task 2: devenv deps + package skeleton

**Files:**
- Modify: `devenv.nix` (add Python packages)
- Create: `src/mrija_client/__init__.py`
- Create: `src/mrija_client/api/__init__.py`
- Create: `src/mrija_client/static/` (empty dir marker)
- Create: `src/mrija_client/templates/` (empty dir marker)

- [ ] **Step 1: Add Python packages to devenv.nix**

Find the `requirements` block in `devenv.nix` (currently has pytest, reportlab, imap-tools) and extend it:

```nix
requirements = ''
  pytest>=8.0
  reportlab>=4.0
  imap-tools>=1.6
  fastapi>=0.110
  uvicorn>=0.29
  jinja2>=3.1
  rich>=13.7
  httpx>=0.27
  python-multipart>=0.0.9
'';
```

- [ ] **Step 2: Reload devenv**

```bash
devenv shell
python -c "import fastapi, uvicorn, jinja2, rich, httpx; print('OK')"
# Expected: OK
```

- [ ] **Step 3: Create package skeleton**

```bash
mkdir -p src/mrija_client/api src/mrija_client/static src/mrija_client/templates
touch src/mrija_client/__init__.py src/mrija_client/api/__init__.py
touch src/mrija_client/static/.gitkeep src/mrija_client/templates/.gitkeep
```

- [ ] **Step 4: Commit skeleton**

```bash
git add devenv.nix src/mrija_client/
git commit -m "feat(client): add package skeleton and Python deps"
```

---

## Task 3: SQLite DB helper

**Files:**
- Create: `src/mrija_client/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_db.py`:

```python
import sqlite3
import pytest
from pathlib import Path
from mrija_client.db import MailDB


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE archive_emails (
            mailbox TEXT, stable_id TEXT, filepath TEXT, folder TEXT,
            date TEXT, from_addr TEXT, to_addrs TEXT, cc_addrs TEXT,
            subject TEXT, body_text TEXT, total_size_bytes INTEGER,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, stable_id)
        );
        CREATE TABLE archive_attachments (
            mailbox TEXT, email_stable_id TEXT, stored_path TEXT,
            sha256 TEXT, size INTEGER, mime TEXT, original_filename TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id, sha256)
        );
        INSERT INTO archive_emails VALUES
            ('box1','id1','f1','Inbox','2024-01-01','alice@x.com','bob@x.com',
             '','Hello world','body text one',1000,'2024-01-01'),
            ('box1','id2','f2','Inbox','2024-01-02','bob@x.com','alice@x.com',
             '','Invoice attached','invoice body',2000,'2024-01-02');
        INSERT INTO archive_attachments VALUES
            ('box1','id2','mailboxes/box1/attachments/abc.pdf',
             'deadbeef',1024,'application/pdf','invoice.pdf','2024-01-02');
    """)
    con.close()
    return MailDB(path)


def test_stats(db):
    s = db.stats()
    assert s["email_count"] == 2
    assert s["attachment_count"] == 1


def test_search_by_subject(db):
    rows = db.search("Invoice")
    assert len(rows) == 1
    assert rows[0]["stable_id"] == "id2"


def test_search_by_from(db):
    rows = db.search("alice@x.com")
    assert len(rows) == 1
    assert rows[0]["stable_id"] == "id1"


def test_search_no_results(db):
    assert db.search("zzz_no_match") == []


def test_browse_by_mailbox(db):
    rows = db.browse("box1")
    assert len(rows) == 2


def test_get_email(db):
    email = db.get_email("box1", "id1")
    assert email is not None
    assert email["subject"] == "Hello world"


def test_get_email_missing(db):
    assert db.get_email("box1", "nope") is None


def test_get_attachments(db):
    atts = db.get_attachments("box1", "id2")
    assert len(atts) == 1
    assert atts[0]["original_filename"] == "invoice.pdf"


def test_get_attachment_by_sha256(db):
    att = db.get_attachment_by_sha256("deadbeef")
    assert att is not None
    assert att["mime"] == "application/pdf"


def test_mailboxes(db):
    boxes = db.mailboxes()
    assert boxes == ["box1"]
```

- [ ] **Step 2: Run tests — expect failure**

```bash
PYTHONPATH=src pytest tests/test_db.py -v 2>&1 | head -20
# Expected: ImportError or ModuleNotFoundError for mrija_client.db
```

- [ ] **Step 3: Implement db.py**

Create `src/mrija_client/db.py`:

```python
from __future__ import annotations
import sqlite3
from pathlib import Path


class MailDB:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row

    def stats(self) -> dict:
        ec = self._con.execute("SELECT COUNT(*) FROM archive_emails").fetchone()[0]
        ac = self._con.execute("SELECT COUNT(*) FROM archive_attachments").fetchone()[0]
        last = self._con.execute(
            "SELECT MAX(date) FROM archive_emails"
        ).fetchone()[0]
        return {"email_count": ec, "attachment_count": ac, "last_updated": last or ""}

    def search(self, q: str, page: int = 0, per_page: int = 50) -> list[dict]:
        pattern = f"%{q}%"
        rows = self._con.execute(
            """SELECT mailbox, stable_id, from_addr, subject, date
               FROM archive_emails
               WHERE subject LIKE ? OR from_addr LIKE ? OR to_addrs LIKE ?
                  OR body_text LIKE ?
               ORDER BY date DESC LIMIT ? OFFSET ?""",
            (pattern, pattern, pattern, pattern, per_page, page * per_page),
        ).fetchall()
        return [dict(r) for r in rows]

    def browse(self, mailbox: str | None, page: int = 0, per_page: int = 50) -> list[dict]:
        if mailbox:
            rows = self._con.execute(
                """SELECT mailbox, stable_id, from_addr, subject, date
                   FROM archive_emails WHERE mailbox = ?
                   ORDER BY date DESC LIMIT ? OFFSET ?""",
                (mailbox, per_page, page * per_page),
            ).fetchall()
        else:
            rows = self._con.execute(
                """SELECT mailbox, stable_id, from_addr, subject, date
                   FROM archive_emails ORDER BY date DESC LIMIT ? OFFSET ?""",
                (per_page, page * per_page),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_email(self, mailbox: str, stable_id: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM archive_emails WHERE mailbox=? AND stable_id=?",
            (mailbox, stable_id),
        ).fetchone()
        return dict(row) if row else None

    def get_attachments(self, mailbox: str, email_stable_id: str) -> list[dict]:
        rows = self._con.execute(
            """SELECT * FROM archive_attachments
               WHERE mailbox=? AND email_stable_id=?""",
            (mailbox, email_stable_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_attachment_by_sha256(self, sha256: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM archive_attachments WHERE sha256=?", (sha256,)
        ).fetchone()
        return dict(row) if row else None

    def mailboxes(self) -> list[str]:
        rows = self._con.execute(
            "SELECT DISTINCT mailbox FROM archive_emails ORDER BY mailbox"
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._con.close()
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
PYTHONPATH=src pytest tests/test_db.py -v
# Expected: 9 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/mrija_client/db.py tests/test_db.py
git commit -m "feat(client): add MailDB SQLite helper with tests"
```

---

## Task 4: App state

**Files:**
- Create: `src/mrija_client/state.py`

No separate test needed — state is a plain dataclass, tested implicitly by later tasks.

- [ ] **Step 1: Create state.py**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mrija_client.db import MailDB


class ClientState(str, Enum):
    NO_DATA  = "no_data"
    STARTING = "starting"
    RUNNING  = "running"
    UPDATING = "updating"
    ERROR    = "error"
    STOPPED  = "stopped"


@dataclass
class AppState:
    state: ClientState = ClientState.NO_DATA
    db: "MailDB | None" = None
    db_path: Path | None = None
    update_progress: int = 0
    update_status: str = ""
    error_message: str = ""
    version: str = ""
```

- [ ] **Step 2: Commit**

```bash
git add src/mrija_client/state.py
git commit -m "feat(client): add AppState dataclass"
```

---

## Task 5: FastAPI server scaffold + static frontend

**Files:**
- Create: `src/mrija_client/server.py`
- Create: `src/mrija_client/static/index.html`
- Create: `src/mrija_client/static/style.css`
- Create: `src/mrija_client/static/htmx.min.js` (downloaded)

- [ ] **Step 1: Download htmx**

```bash
curl -sL "https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js" \
  -o src/mrija_client/static/htmx.min.js
wc -c src/mrija_client/static/htmx.min.js
# Expected: ~47000 bytes
```

- [ ] **Step 2: Create index.html**

Create `src/mrija_client/static/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MrijaArchive</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="/static/htmx.min.js"></script>
</head>
<body>
  <header>
    <h1>MrijaArchive</h1>
    <div class="search-bar">
      <input
        type="search"
        name="q"
        placeholder="Search emails…"
        hx-get="/data/search"
        hx-trigger="keyup changed delay:300ms, search"
        hx-target="#results"
        hx-indicator="#spinner"
        autofocus>
      <span id="spinner" class="htmx-indicator">…</span>
    </div>
    <nav>
      <a hx-get="/data/browse" hx-target="#results" hx-push-url="true">Browse</a>
    </nav>
  </header>
  <div id="layout">
    <main id="results">
      <p class="hint">Type to search or click Browse.</p>
    </main>
    <aside id="detail"></aside>
  </div>
</body>
</html>
```

- [ ] **Step 3: Create style.css**

Create `src/mrija_client/static/style.css`:

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #111827; color: #e0e7ff; font-family: system-ui, sans-serif;
       font-size: 14px; display: flex; flex-direction: column; height: 100vh; }
header { padding: .75rem 1rem; background: #1f2937; border-bottom: 1px solid #374151;
         display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
h1 { font-size: 1rem; font-weight: 700; color: #818cf8; white-space: nowrap; }
.search-bar { flex: 1; display: flex; gap: .5rem; align-items: center; }
input[type=search] { flex: 1; background: #374151; border: 1px solid #4b5563;
  color: #e0e7ff; padding: .4rem .75rem; border-radius: 6px; font-size: 14px; }
input[type=search]:focus { outline: 2px solid #818cf8; }
nav a { color: #a5b4fc; text-decoration: none; cursor: pointer; }
nav a:hover { color: #e0e7ff; }
.htmx-indicator { font-size: 12px; color: #6b7280; display: none; }
.htmx-request .htmx-indicator { display: inline; }
#layout { display: flex; flex: 1; overflow: hidden; }
#results { flex: 1; overflow-y: auto; padding: .5rem; }
#detail { width: 42%; overflow-y: auto; padding: 1rem;
          border-left: 1px solid #374151; background: #1f2937; }
.email-row { padding: .5rem .75rem; border-radius: 6px; cursor: pointer;
             display: grid; grid-template-columns: 16rem 1fr 7rem; gap: .5rem;
             border-bottom: 1px solid #1f2937; }
.email-row:hover { background: #1f2937; }
.from { color: #a5b4fc; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.subject { color: #e0e7ff; overflow: hidden; text-overflow: ellipsis; }
.date { color: #6b7280; text-align: right; white-space: nowrap; }
.hint, .empty { color: #6b7280; padding: 2rem; text-align: center; }
.email-detail h2 { font-size: .95rem; color: #e0e7ff; margin-bottom: .75rem; }
.meta { color: #9ca3af; font-size: 12px; margin-bottom: .5rem; }
.body-text { white-space: pre-wrap; color: #d1d5db; font-size: 13px;
             margin-top: .75rem; line-height: 1.6; }
.attachments { margin-top: 1rem; }
.att-item { color: #a5b4fc; text-decoration: none; display: block;
            padding: .25rem 0; font-size: 13px; }
.att-item:hover { color: #e0e7ff; }
```

- [ ] **Step 4: Create server.py**

Create `src/mrija_client/server.py`:

```python
from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from mrija_client.state import AppState

_HERE = Path(__file__).parent
STATIC_DIR = _HERE / "static"
TEMPLATE_DIR = _HERE / "templates"

_app_state: AppState | None = None


def get_state() -> AppState:
    assert _app_state is not None, "call create_app first"
    return _app_state


def create_app(state: AppState) -> FastAPI:
    global _app_state
    _app_state = state

    from mrija_client.api.data import router as data_router
    from mrija_client.api.control import router as control_router

    app = FastAPI(title="MrijaArchive", docs_url="/api/docs", openapi_url="/openapi.json")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(data_router, prefix="/data")
    app.include_router(control_router, prefix="/api")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return app
```

- [ ] **Step 5: Verify server starts**

```bash
PYTHONPATH=src python -c "
from mrija_client.state import AppState
from mrija_client.server import create_app
app = create_app(AppState())
print('OK — routes:', [r.path for r in app.routes])
"
# Expected: OK — routes: ['/static', '/data/...', '/api/...', '/']
```

- [ ] **Step 6: Commit**

```bash
git add src/mrija_client/server.py src/mrija_client/static/
git commit -m "feat(client): FastAPI server scaffold + HTMX static frontend"
```

---

## Task 6: Search + browse routes

**Files:**
- Create: `src/mrija_client/api/data.py`
- Create: `src/mrija_client/templates/search_results.html`
- Create: `src/mrija_client/templates/browse.html`
- Create: `src/mrija_client/templates/email_detail.html`
- Create: `tests/test_data_routes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_data_routes.py`:

```python
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from mrija_client.state import AppState, ClientState
from mrija_client.db import MailDB
from mrija_client.server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.sqlite"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE archive_emails (
            mailbox TEXT, stable_id TEXT, filepath TEXT, folder TEXT,
            date TEXT, from_addr TEXT, to_addrs TEXT, cc_addrs TEXT,
            subject TEXT, body_text TEXT, total_size_bytes INTEGER,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, stable_id)
        );
        CREATE TABLE archive_attachments (
            mailbox TEXT, email_stable_id TEXT, stored_path TEXT,
            sha256 TEXT, size INTEGER, mime TEXT, original_filename TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id, sha256)
        );
        INSERT INTO archive_emails VALUES
            ('box1','id1','f1','Inbox','2024-01-01','alice@x.com','bob@x.com',
             '','Hello world','body text',1000,'2024-01-01'),
            ('box1','id2','f2','Inbox','2024-01-02','bob@x.com','alice@x.com',
             '','Invoice attached','invoice',2000,'2024-01-02');
        INSERT INTO archive_attachments VALUES
            ('box1','id2','mailboxes/box1/attachments/inv.pdf',
             'abc123',1024,'application/pdf','invoice.pdf','2024-01-02');
    """)
    con.close()
    state = AppState(state=ClientState.RUNNING, db=MailDB(db_path), db_path=db_path)
    return TestClient(create_app(state))


def test_search_returns_html(client):
    r = client.get("/data/search?q=Invoice")
    assert r.status_code == 200
    assert "Invoice attached" in r.text
    assert "<article" in r.text


def test_search_empty_returns_empty_state(client):
    r = client.get("/data/search?q=zzz_no_match")
    assert r.status_code == 200
    assert "No results" in r.text


def test_browse_returns_all_emails(client):
    r = client.get("/data/browse?mailbox=box1")
    assert r.status_code == 200
    assert "Hello world" in r.text
    assert "Invoice attached" in r.text


def test_email_detail(client):
    r = client.get("/data/email/box1/id2")
    assert r.status_code == 200
    assert "Invoice attached" in r.text
    assert "invoice.pdf" in r.text


def test_email_detail_missing(client):
    r = client.get("/data/email/box1/nope")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests — expect failure**

```bash
PYTHONPATH=src pytest tests/test_data_routes.py -v 2>&1 | head -15
# Expected: ImportError for mrija_client.api.data
```

- [ ] **Step 3: Create Jinja2 templates**

Create `src/mrija_client/templates/search_results.html`:

```html
{% for email in emails %}
<article class="email-row"
         hx-get="/data/email/{{ email.mailbox }}/{{ email.stable_id }}"
         hx-target="#detail"
         hx-swap="innerHTML">
  <span class="from">{{ email.from_addr }}</span>
  <span class="subject">{{ email.subject }}</span>
  <span class="date">{{ email.date[:10] if email.date else "" }}</span>
</article>
{% else %}
<p class="empty">No results.</p>
{% endfor %}
```

Create `src/mrija_client/templates/browse.html`:

```html
<p class="hint" style="padding:.5rem 0 .75rem">
  Mailbox: <strong>{{ mailbox or "all" }}</strong> — {{ emails|length }} shown
</p>
{% for email in emails %}
<article class="email-row"
         hx-get="/data/email/{{ email.mailbox }}/{{ email.stable_id }}"
         hx-target="#detail"
         hx-swap="innerHTML">
  <span class="from">{{ email.from_addr }}</span>
  <span class="subject">{{ email.subject }}</span>
  <span class="date">{{ email.date[:10] if email.date else "" }}</span>
</article>
{% else %}
<p class="empty">No emails.</p>
{% endfor %}
```

Create `src/mrija_client/templates/email_detail.html`:

```html
<div class="email-detail">
  <h2>{{ email.subject }}</h2>
  <p class="meta">From: {{ email.from_addr }}</p>
  <p class="meta">To: {{ email.to_addrs }}</p>
  {% if email.cc_addrs %}<p class="meta">CC: {{ email.cc_addrs }}</p>{% endif %}
  <p class="meta">Date: {{ email.date }}</p>
  <p class="meta">Mailbox: {{ email.mailbox }}</p>
  {% if attachments %}
  <div class="attachments">
    <p class="meta">Attachments:</p>
    {% for att in attachments %}
    <a class="att-item" href="/data/attachment/{{ att.sha256 }}" download="{{ att.original_filename }}">
      📎 {{ att.original_filename }} ({{ (att.size / 1024)|round(1) }} KB)
    </a>
    {% endfor %}
  </div>
  {% endif %}
  <pre class="body-text">{{ email.body_text }}</pre>
</div>
```

- [ ] **Step 4: Create data.py router**

Create `src/mrija_client/api/data.py`:

```python
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from mrija_client.server import get_state

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _render(name: str, **ctx) -> HTMLResponse:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)
    return HTMLResponse(env.get_template(name).render(**ctx))


@router.get("/search", response_class=HTMLResponse)
async def search(q: str = ""):
    state = get_state()
    emails = state.db.search(q) if state.db and q.strip() else []
    return _render("search_results.html", emails=emails)


@router.get("/browse", response_class=HTMLResponse)
async def browse(mailbox: str = ""):
    state = get_state()
    emails = state.db.browse(mailbox or None) if state.db else []
    return _render("browse.html", emails=emails, mailbox=mailbox)


@router.get("/email/{mailbox}/{stable_id}", response_class=HTMLResponse)
async def email_detail(mailbox: str, stable_id: str):
    state = get_state()
    if not state.db:
        raise HTTPException(503, "No database loaded")
    email = state.db.get_email(mailbox, stable_id)
    if not email:
        raise HTTPException(404, "Email not found")
    attachments = state.db.get_attachments(mailbox, stable_id)
    return _render("email_detail.html", email=email, attachments=attachments)


@router.get("/attachment/{sha256}")
async def download_attachment(sha256: str):
    state = get_state()
    if not state.db:
        raise HTTPException(503, "No database loaded")
    att = state.db.get_attachment_by_sha256(sha256)
    if not att:
        raise HTTPException(404, "Attachment not found")
    # stored_path is relative to data/ dir (sibling of client/ dir)
    data_dir = state.db_path.parent.parent
    file_path = (data_dir / att["stored_path"]).resolve()
    # Path traversal guard
    if not str(file_path).startswith(str(data_dir.resolve())):
        raise HTTPException(403, "Forbidden")
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(
        file_path,
        filename=att["original_filename"],
        media_type=att["mime"] or "application/octet-stream",
    )
```

- [ ] **Step 5: Run tests — expect all pass**

```bash
PYTHONPATH=src pytest tests/test_data_routes.py -v
# Expected: 5 passed
```

- [ ] **Step 6: Commit**

```bash
git add src/mrija_client/api/data.py src/mrija_client/templates/ tests/test_data_routes.py
git commit -m "feat(client): /data/* HTMX routes and Jinja2 templates"
```

---

## Task 7: Control API

**Files:**
- Create: `src/mrija_client/api/control.py`
- Create: `tests/test_control_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_control_api.py`:

```python
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from mrija_client.state import AppState, ClientState
from mrija_client.db import MailDB
from mrija_client.server import create_app

API_KEY = "test-key"


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("MRIJA_API_KEY", API_KEY)


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.sqlite"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE archive_emails (
            mailbox TEXT, stable_id TEXT, filepath TEXT, folder TEXT,
            date TEXT, from_addr TEXT, to_addrs TEXT, cc_addrs TEXT,
            subject TEXT, body_text TEXT, total_size_bytes INTEGER,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, stable_id)
        );
        CREATE TABLE archive_attachments (
            mailbox TEXT, email_stable_id TEXT, stored_path TEXT,
            sha256 TEXT, size INTEGER, mime TEXT, original_filename TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id, sha256)
        );
        INSERT INTO archive_emails VALUES
            ('box1','id1','f1','Inbox','2024-01-01','alice@x.com','bob@x.com',
             '','Hello','body',1000,'2024-01-01');
    """)
    con.close()
    state = AppState(state=ClientState.RUNNING, db=MailDB(db_path), db_path=db_path)
    return TestClient(create_app(state))


def auth():
    return {"X-Api-Key": API_KEY}


def test_status_ok(client):
    r = client.get("/api/status", headers=auth())
    assert r.status_code == 200
    j = r.json()
    assert j["state"] == "running"
    assert j["email_count"] == 1
    assert "attachment_count" in j
    assert "last_updated" in j


def test_status_no_auth(client):
    r = client.get("/api/status")
    assert r.status_code == 401


def test_status_wrong_key(client):
    r = client.get("/api/status", headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


def test_open_local_file(client, tmp_path):
    db_path = tmp_path / "new.sqlite"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE archive_emails (
            mailbox TEXT, stable_id TEXT, filepath TEXT, folder TEXT,
            date TEXT, from_addr TEXT, to_addrs TEXT, cc_addrs TEXT,
            subject TEXT, body_text TEXT, total_size_bytes INTEGER,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, stable_id)
        );
        CREATE TABLE archive_attachments (
            mailbox TEXT, email_stable_id TEXT, stored_path TEXT,
            sha256 TEXT, size INTEGER, mime TEXT, original_filename TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id, sha256)
        );
    """)
    con.close()
    r = client.post("/api/open", json={"path": str(db_path)}, headers=auth())
    assert r.status_code == 200
    assert r.json()["state"] == "running"


def test_open_missing_file(client):
    r = client.post("/api/open", json={"path": "/nonexistent/file.sqlite"}, headers=auth())
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests — expect failure**

```bash
PYTHONPATH=src pytest tests/test_control_api.py -v 2>&1 | head -15
# Expected: ImportError for mrija_client.api.control
```

- [ ] **Step 3: Create control.py**

Create `src/mrija_client/api/control.py`:

```python
from __future__ import annotations
import os
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from pathlib import Path
from mrija_client.server import get_state
from mrija_client.state import AppState, ClientState

router = APIRouter()


def _check_key(x_api_key: str = Header(default="")):
    expected = os.environ.get("MRIJA_API_KEY", "dev-key")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


class OpenRequest(BaseModel):
    path: str


@router.get("/status", dependencies=[Depends(_check_key)])
async def status():
    state = get_state()
    stats = state.db.stats() if state.db else {"email_count": 0, "attachment_count": 0, "last_updated": ""}
    return {
        "state": state.state.value,
        "email_count": stats["email_count"],
        "attachment_count": stats["attachment_count"],
        "last_updated": stats["last_updated"],
        "db_path": str(state.db_path) if state.db_path else None,
        "version": state.version,
    }


@router.post("/open", dependencies=[Depends(_check_key)])
async def open_file(req: OpenRequest):
    from mrija_client.db import MailDB
    state = get_state()
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")
    if state.db:
        state.db.close()
    state.db = MailDB(path)
    state.db_path = path
    state.state = ClientState.RUNNING
    return {"state": state.state.value, "db_path": str(path)}


@router.post("/restart", dependencies=[Depends(_check_key)])
async def restart():
    state = get_state()
    if state.db:
        state.db.close()
        state.db = None
    if state.db_path and state.db_path.exists():
        from mrija_client.db import MailDB
        state.db = MailDB(state.db_path)
        state.state = ClientState.RUNNING
    else:
        state.state = ClientState.NO_DATA
    return {"state": state.state.value}


@router.post("/shutdown", dependencies=[Depends(_check_key)])
async def shutdown():
    import threading
    state = get_state()
    state.state = ClientState.STOPPED
    def _stop():
        import time, os, signal
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_stop, daemon=True).start()
    return {"state": "stopped"}
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
PYTHONPATH=src pytest tests/test_control_api.py -v
# Expected: 6 passed
```

- [ ] **Step 5: Run full test suite**

```bash
PYTHONPATH=src pytest tests/ -v --ignore=tests/test_launcher.py
# Expected: all pass (test_launcher.py references old launcher — update in Phase 2)
```

- [ ] **Step 6: Commit**

```bash
git add src/mrija_client/api/control.py tests/test_control_api.py
git commit -m "feat(client): /api/* control endpoints with X-Api-Key auth"
```

---

## Task 8: Updater

**Files:**
- Create: `src/mrija_client/updater.py`
- Create: `tests/test_updater.py`
- Modify: `src/mrija_client/api/control.py` (add /api/update routes)

- [ ] **Step 1: Write failing tests**

Create `tests/test_updater.py`:

```python
import gzip
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from mrija_client.updater import fetch_manifest, verify_sha256, decompress_gz
import hashlib


def test_verify_sha256_match(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello")
    expected = hashlib.sha256(b"hello").hexdigest()
    assert verify_sha256(f, expected) is True


def test_verify_sha256_mismatch(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello")
    assert verify_sha256(f, "deadbeef") is False


def test_decompress_gz(tmp_path):
    gz = tmp_path / "test.sqlite.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(b"SQLite data")
    result = decompress_gz(gz)
    assert result == tmp_path / "test.sqlite"
    assert result.read_bytes() == b"SQLite data"
    assert not gz.exists()


def test_fetch_manifest_parses_json():
    manifest = {"version": "20260613T000000Z", "sha256": "abc", "url": "/updates/f.gz", "filename": "f.gz"}
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(manifest).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_response):
        result = fetch_manifest("http://example.com/updates/manifest.json")
    assert result["version"] == "20260613T000000Z"
```

- [ ] **Step 2: Run tests — expect failure**

```bash
PYTHONPATH=src pytest tests/test_updater.py -v 2>&1 | head -15
# Expected: ImportError for mrija_client.updater
```

- [ ] **Step 3: Create updater.py**

Create `src/mrija_client/updater.py`:

```python
from __future__ import annotations
import gzip
import hashlib
import json
import urllib.request
from pathlib import Path


UPDATE_SERVER = "http://104.248.242.243"
MANIFEST_PATH = "/updates/manifest.json"


def fetch_manifest(url: str | None = None) -> dict:
    target = url or (UPDATE_SERVER + MANIFEST_PATH)
    with urllib.request.urlopen(target, timeout=10) as r:
        return json.loads(r.read())


def verify_sha256(path: Path, expected: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest() == expected


def decompress_gz(gz_path: Path) -> Path:
    out_path = gz_path.with_suffix("")  # removes .gz
    with gzip.open(gz_path, "rb") as gz_in, open(out_path, "wb") as f_out:
        while chunk := gz_in.read(65536):
            f_out.write(chunk)
    gz_path.unlink()
    return out_path


def download_archive(
    url: str,
    dest: Path,
    on_progress: "Callable[[int, int], None] | None" = None,
) -> None:
    with urllib.request.urlopen(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            while chunk := r.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and total:
                    on_progress(downloaded, total)


def run_update(state: "AppState", dest_dir: Path) -> None:
    from mrija_client.state import ClientState
    from mrija_client.db import MailDB

    state.state = ClientState.UPDATING
    state.update_progress = 0
    state.update_status = "Fetching manifest…"

    try:
        manifest = fetch_manifest()
        url = UPDATE_SERVER + manifest["url"]
        gz_dest = dest_dir / manifest["filename"]
        dest_dir.mkdir(parents=True, exist_ok=True)

        state.update_status = "Downloading archive…"

        def _progress(done: int, total: int) -> None:
            state.update_progress = int(done / total * 90)

        download_archive(url, gz_dest, _progress)

        state.update_status = "Verifying checksum…"
        if not verify_sha256(gz_dest, manifest["sha256"]):
            raise ValueError("SHA256 mismatch — download corrupted")

        state.update_status = "Decompressing…"
        state.update_progress = 92
        sqlite_path = decompress_gz(gz_dest)

        state.update_status = "Applying update…"
        state.update_progress = 97
        if state.db:
            state.db.close()
        state.db = MailDB(sqlite_path)
        state.db_path = sqlite_path
        state.version = manifest.get("version", "")

        state.update_progress = 100
        state.update_status = "Done"
        state.state = ClientState.RUNNING

    except Exception as exc:
        from mrija_client.state import ClientState
        state.state = ClientState.ERROR
        state.error_message = str(exc)
        state.update_status = f"Error: {exc}"
        raise
```

- [ ] **Step 4: Run updater tests — expect all pass**

```bash
PYTHONPATH=src pytest tests/test_updater.py -v
# Expected: 4 passed
```

- [ ] **Step 5: Add /api/update routes to control.py**

Add these imports at top of `src/mrija_client/api/control.py`:

```python
import asyncio
import json
import threading
```

Add these routes at the bottom of `src/mrija_client/api/control.py`:

```python
@router.post("/update", dependencies=[Depends(_check_key)])
async def trigger_update():
    import tempfile
    state = get_state()
    if state.state.value == "updating":
        raise HTTPException(409, "Update already in progress")
    dest_dir = state.db_path.parent if state.db_path else Path(tempfile.mkdtemp())

    from mrija_client.updater import run_update
    threading.Thread(target=run_update, args=(state, dest_dir), daemon=True).start()
    return {"status": "started"}


from fastapi.responses import StreamingResponse

@router.get("/update/progress", dependencies=[Depends(_check_key)])
async def update_progress():
    state = get_state()

    async def _generate():
        from mrija_client.state import ClientState
        while state.state == ClientState.UPDATING:
            payload = json.dumps({
                "percent": state.update_progress,
                "status": state.update_status,
            })
            yield f"data: {payload}\n\n"
            await asyncio.sleep(0.5)
        payload = json.dumps({"percent": 100, "status": state.update_status})
        yield f"data: {payload}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")
```

- [ ] **Step 6: Run full test suite**

```bash
PYTHONPATH=src pytest tests/ -v --ignore=tests/test_launcher.py
# Expected: all pass
```

- [ ] **Step 7: Commit**

```bash
git add src/mrija_client/updater.py tests/test_updater.py src/mrija_client/api/control.py
git commit -m "feat(client): updater with SHA256 verify + /api/update SSE progress"
```

---

## Task 9: Linux TUI + entry point

**Files:**
- Create: `src/mrija_client/tui.py`
- Create: `src/mrija_client/__main__.py`

- [ ] **Step 1: Create tui.py**

Create `src/mrija_client/tui.py`:

```python
from __future__ import annotations
import time
import threading
import webbrowser
from mrija_client.state import AppState, ClientState

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text
    _RICH = True
except ImportError:
    _RICH = False


def _make_panel(state: AppState, server_url: str) -> "Panel":
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold cyan", min_width=14)
    t.add_column()

    state_color = {"running": "green", "updating": "yellow",
                   "error": "red", "no_data": "dim"}.get(state.state.value, "white")
    t.add_row("State", Text(state.state.value, style=state_color))

    if state.db:
        stats = state.db.stats()
        t.add_row("Emails", str(stats["email_count"]))
        t.add_row("Attachments", str(stats["attachment_count"]))
        t.add_row("Last updated", stats["last_updated"] or "—")

    if state.state == ClientState.UPDATING:
        bar_filled = int(state.update_progress / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        t.add_row("Progress", f"[{bar}] {state.update_progress}%")
        t.add_row("Status", state.update_status)

    if state.error_message:
        t.add_row("Error", Text(state.error_message, style="red"))

    t.add_row("Server", server_url)
    t.add_row("Keys", "[q] quit  [u] update  [b] browser")

    return Panel(t, title="[bold]MrijaArchive[/bold]", border_style="dim blue")


def run_tui(state: AppState, server_url: str) -> None:
    if not _RICH:
        print(f"Server running at {server_url} — Ctrl+C to stop")
        try:
            while state.state != ClientState.STOPPED:
                time.sleep(1)
        except KeyboardInterrupt:
            state.state = ClientState.STOPPED
        return

    console = Console()
    stop_event = threading.Event()

    def _keys():
        import sys
        while not stop_event.is_set():
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                state.state = ClientState.STOPPED
                stop_event.set()
                break
            if line.lower() == "q":
                state.state = ClientState.STOPPED
                stop_event.set()
            elif line.lower() == "u":
                import tempfile
                from mrija_client.updater import run_update
                dest = state.db_path.parent if state.db_path else __import__("pathlib").Path(tempfile.mkdtemp())
                threading.Thread(target=run_update, args=(state, dest), daemon=True).start()
            elif line.lower() == "b":
                webbrowser.open(server_url)

    key_thread = threading.Thread(target=_keys, daemon=True)
    key_thread.start()

    try:
        with Live(console=console, refresh_per_second=2) as live:
            while state.state != ClientState.STOPPED:
                live.update(_make_panel(state, server_url))
                time.sleep(0.5)
    except KeyboardInterrupt:
        state.state = ClientState.STOPPED
    finally:
        stop_event.set()
```

- [ ] **Step 2: Create __main__.py**

Create `src/mrija_client/__main__.py`:

```python
from __future__ import annotations
import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="MrijaArchive client")
    parser.add_argument("--db", type=Path, help="Path to mail_archive.sqlite")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--no-tui", action="store_true", help="Skip Rich TUI")
    args = parser.parse_args()

    from mrija_client.state import AppState, ClientState
    from mrija_client.server import create_app

    state = AppState()

    if args.db:
        if not args.db.exists():
            print(f"ERROR: database not found: {args.db}", file=sys.stderr)
            sys.exit(1)
        from mrija_client.db import MailDB
        state.db = MailDB(args.db)
        state.db_path = args.db
        state.state = ClientState.RUNNING

    app = create_app(state)
    server_url = f"http://{args.bind}:{args.port}"

    api_key = os.environ.get("MRIJA_API_KEY")
    if not api_key:
        import secrets
        api_key = secrets.token_hex(16)
        os.environ["MRIJA_API_KEY"] = api_key
        print(f"API key (set MRIJA_API_KEY to fix): {api_key}")

    import uvicorn
    config = uvicorn.Config(app, host=args.bind, port=args.port, log_level="warning")
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    if not _wait_for_server(server_url):
        print("ERROR: server did not start in time", file=sys.stderr)
        sys.exit(1)

    webbrowser.open(server_url)

    if args.no_tui:
        print(f"Server running at {server_url}  (Ctrl+C to stop)")
        try:
            t.join()
        except KeyboardInterrupt:
            pass
    else:
        from mrija_client.tui import run_tui
        run_tui(state, server_url)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke test the full client**

```bash
PYTHONPATH=src MRIJA_API_KEY=dev python -m mrija_client \
  --db data/client/mail_archive.sqlite --no-tui --port 8090
# Expected: prints "Server running at http://127.0.0.1:8090", browser opens
# Ctrl+C to stop
```

- [ ] **Step 4: Test with TUI**

```bash
PYTHONPATH=src MRIJA_API_KEY=dev python -m mrija_client \
  --db data/client/mail_archive.sqlite --port 8090
# Expected: Rich panel shows state=running, email count, server URL
# Type 'b' then Enter to open browser, 'q' then Enter to quit
```

- [ ] **Step 5: Verify API**

In a second terminal while the client is running:

```bash
curl -s -H "X-Api-Key: dev" http://localhost:8090/api/status | python -m json.tool
# Expected: {"state": "running", "email_count": 29402, ...}
```

- [ ] **Step 6: Run full test suite one final time**

```bash
PYTHONPATH=src pytest tests/ -v --ignore=tests/test_launcher.py
# Expected: all pass
```

- [ ] **Step 7: Commit**

```bash
git add src/mrija_client/tui.py src/mrija_client/__main__.py
git commit -m "feat(client): Rich TUI + python -m mrija_client entry point"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Repo cleanup (Task 1)
- ✅ FastAPI server + HTMX frontend (Tasks 5–6)
- ✅ `/data/search`, `/data/browse`, `/data/email/{id}`, `/data/attachment/{sha}` (Task 6)
- ✅ `/api/status`, `/api/update`, `/api/update/progress`, `/api/open`, `/api/restart`, `/api/shutdown` (Tasks 7–8)
- ✅ `X-Api-Key` auth (Task 7)
- ✅ Updater: manifest fetch, download, SHA256 verify, decompress, swap DB (Task 8)
- ✅ Linux TUI with Rich (Task 9)
- ✅ `python -m mrija_client --db <path>` entry point (Task 9)
- ⏭ Postman collection → Phase 2
- ⏭ Windows pywebview wrapper → Phase 2
- ⏭ DO remote bind option → Phase 2

**Type consistency:** `AppState` used in db.py, server.py, control.py, updater.py, tui.py, __main__.py — all reference same import path `mrija_client.state`. `MailDB` created in db.py, referenced in control.py and updater.py via same path. `get_state()` defined in server.py, imported in data.py and control.py consistently.

**No placeholders:** All steps contain actual code. All commands show expected output.

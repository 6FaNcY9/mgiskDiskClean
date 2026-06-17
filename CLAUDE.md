# CLAUDE.md

This file gives coding agents the current project shape and the commands that
match this checkout.

## What This Is

MrijaArchive is a Python mailbox archive pipeline and local archive viewer for
mrija.org.

- `src/maildir_report/` parses Maildir messages, extracts attachments, builds
  deterministic IDs, and writes SQLite indexes.
- `src/mrija_client/` is the local FastAPI + HTMX + Jinja2 client. It reads the
  SQLite archive directly and serves search, browse, filters, status, updates,
  email detail, and attachment download routes.
- `launcher/windows/` builds `MrijaArchive.exe`, a pywebview wrapper around the
  FastAPI server.

The active runtime is Python + SQLite. Do not assume a `web/` PHP app, Docker
Compose stack, MySQL database, or Composer workflow exists unless you verify it
in the tree first.

## Commands

```bash
# Full test suite
python -B -m pytest tests -q

# Source client
python -B -m mrija_client --db data/index/mail_index.sqlite --no-tui

# Common focused tests
python -B -m pytest tests/test_db.py -q
python -B -m pytest tests/test_data_routes.py tests/test_control_api.py -q
python -B -m pytest tests/test_launcher.py tests/test_updater.py -q
```

Use `rtk` before shell commands when working through Codex in this repository.

### Windows

```powershell
.\dev\windows\setup-dev.ps1
.\dev\windows\load-env.ps1
.\dev\windows\build-client-db.ps1
.\dev\windows\run-client.ps1
.\dev\windows\test.ps1
```

```bat
cd launcher\windows
build.bat
package.bat
```

## Architecture

```text
Maildir source
  -> src/maildir_report.extract_attachments
  -> src/maildir_report.index_mailbox
  -> data/index/mail_index.sqlite
  -> src/mrija_client.db.MailDB
  -> FastAPI routes in src/mrija_client/api/
  -> HTMX fragments in src/mrija_client/templates/
```

`MailDB` owns SQLite access. Keep filtering and pagination in SQL with
parameterized queries.

`create_app()` in `src/mrija_client/server.py` stores an `AppState` instance for
route handlers. Routers import `get_state()` lazily inside handlers to avoid
cycles.

## Key Files

| Path | Purpose |
|------|---------|
| `src/maildir_report/parser.py` | RFC-822 parsing and body/address extraction |
| `src/maildir_report/index_mailbox.py` | SQLite index build and migration |
| `src/maildir_report/extract_attachments.py` | MIME attachment extraction |
| `src/mrija_client/db.py` | SQLite query wrapper for the client |
| `src/mrija_client/server.py` | FastAPI app factory and app shell |
| `src/mrija_client/api/data.py` | HTML data/detail/status routes |
| `src/mrija_client/api/control.py` | API-key protected control/update routes |
| `launcher/windows/app.py` | pywebview Windows launcher |

## Constraints

- Do not commit generated `data/`, `logs/`, `reports/`, package ZIPs, caches, or
  local env files.
- Keep the Windows package Docker-free and PHP-free.
- The end-user UI is intentionally local-only and binds to `127.0.0.1`.
- Real server sync requires SSH key access; password-based automation is
  intentionally unsupported.

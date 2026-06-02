# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A mailbox archive pipeline for mrija.org. A Python pipeline (in `src/`) syncs Maildir data from a remote server, indexes emails into SQLite/MySQL, and extracts attachments. A frameworkless PHP 8 web app (in `web/`) serves a search UI over the indexed data. No Composer, no PHP framework.

**Canonical local runtime: Docker Compose.** `devenv` exists only as an optional contributor convenience path.

## Commands

### Docker Compose (canonical)

```bash
# First-time setup
cp .env.example .env          # then fill in MRIJA_* values

# Start the stack
docker compose up -d --build

# Run DB migrations
docker compose run --rm app php web/src/cli/migrate.php

# Import archive data from SQLite → MySQL
docker compose run --rm app php web/src/cli/import_archive.php --sqlite /app/data/index/mail_index.sqlite

# Run QA (fixture-based, no server needed)
docker compose run --rm app bash docker/qa-archive-docker.sh

# Search the archive
docker compose run --rm app php web/src/cli/search_archive.php --query "invoice"

# Web UI
xdg-open http://localhost:${MRIJA_WEB_PORT:-8080}

# Teardown
docker compose down
```

### Python tests

```bash
# Run all tests (inside devenv or with PYTHONPATH set)
pytest tests/ -q

# Run a single test file
pytest tests/test_body_cc_extraction.py -v
```

### devenv (optional — contributors only)

```bash
devenv shell

# Sync all mailboxes from real server (requires SSH key auth)
sync-all --mailboxes-file data/mailboxes.txt

# Sync a single mailbox
sync-all --mailbox gabriel.hangel

# Dry-run with local fixture data
sync-all --mailboxes-file tests/fixtures/mailboxes.txt \
         --src-base tests/fixtures/src \
         --skip-import

# Per-mailbox operations
extract-attachments <mailbox>
index-mailbox <mailbox>
index-all

# DB (devenv-managed MariaDB)
db-start
db-migrate
```

## Architecture

### Data flow

```
Remote IMAP/rsync
      ↓
data/mailboxes/<name>/maildir/.maildir/   ← raw Maildir
      ↓ extract_attachments.py
data/mailboxes/<name>/attachments/        ← extracted files
      ↓ index_mailbox.py (schema v2)
data/mailboxes/<name>/index.sqlite        ← per-mailbox SQLite
data/index/mail_index.sqlite              ← global SQLite (merged)
      ↓ import_archive.php
MySQL: archive_emails + archive_attachments (FULLTEXT index)
      ↓
web/public/index.php                      ← PHP search UI (single file)
```

### Python pipeline (`src/maildir_report/`)

| File | Role |
|------|------|
| `parser.py` | RFC-822 parsing; extracts `from`, `subject`, `date`, `to`, `cc`, `body_text` |
| `extract_attachments.py` | MIME attachment extraction; idempotent |
| `index_mailbox.py` | Builds SQLite indexes (schema v2, includes v1→v2 migration) |
| `dedup.py` | Duplicate grouping logic |
| `hash.py` | SHA-256 helpers |
| `ids.py` | Stable ID generation (deterministic per message) |
| `walk.py` | Maildir walking with deterministic ordering |
| `models.py` | Shared dataclasses |

`src/tui/` exists but is **not a supported runtime path** — treat as experimental.

### PHP web app (`web/`)

- **Single-file entry point:** `web/public/index.php` handles all search, browse, and export requests
- **Auth:** `web/src/Auth/` — session management + CSRF guard
- **Attachment download:** `web/public/download.php` + `web/src/Download/DownloadService.php`
- **Config:** `web/config/local.php` (never committed; based on `local.php.example`)
- **Docker config:** `web/config/local.php.docker` (non-secret; real creds come from Compose env vars)
- **Migrations:** `web/migrations/001_archive_schema.sql` — run once via `migrate.php`

### Database schema

Two MySQL tables in `mailreview` schema:
- `archive_emails` — one row per email; FULLTEXT index on `subject`, `from_addr`, `to_addrs`, `cc_addrs`, `body_text`
- `archive_attachments` — one row per (email, attachment) pair; keyed by SHA-256

### Config file (`web/config/local.php`)

Required keys: `db.host/port/dbname/user/password`, `coworker_password_hash`, `admin_password_hash`. Generate bcrypt hashes with:
```bash
docker compose run --rm app php -r "echo password_hash('yourpassword', PASSWORD_BCRYPT), PHP_EOL;"
```

## Key Constraints

- Real server sync (`sync-all` without `--src-base`) requires SSH key auth to `mrija_org@s16.thehost.com.ua` — password-based auth was intentionally removed.
- `data/`, `logs/`, `reports/` are gitignored and never committed.
- `web/config/local.php` is gitignored and never committed.
- The `web` service in Compose mounts `./web` as a live-reload volume — PHP changes are reflected immediately without rebuild.
- `data/mailboxes.txt` must be populated with real mailbox names before a production sync.

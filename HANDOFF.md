# Handoff — Mailbox Archive Pipeline

**Branch:** `feature/mailbox-archive`
**Worktree:** `.worktrees/mailbox-archive`
**Tests:** 253 passing / 0 failing
**QA:** All 8 steps passed (fixture-based, no server access)

---

## What Was Built

A complete local email archive pipeline for mrija.org mailboxes:

1. **`sync-all`** — rsync all mailboxes from `mrija_org@s16.thehost.com.ua:email/mrija.org/`
   Real server sync now requires SSH keys. Password-in-`.env` support was removed on purpose.
   Flags: `--mailboxes-file`, `--src-base` (local path for testing), `--skip-import`, `--mailbox`

2. **Parser** (`src/maildir_report/parser.py`) — now extracts `cc_addrs` and `body_text` per email

3. **SQLite indexer** (`src/maildir_report/index_mailbox.py`) — schema v2 with `to_addrs`, `cc_addrs`, `body_text`; automatic v1→v2 in-place migration; WAL checkpoint after bulk indexing

4. **MySQL importer** (`web/src/cli/import_archive.php`) — chunked upsert from global `mail_index.sqlite` into `archive_emails` + `archive_attachments`

5. **Search CLI** (`web/src/cli/search_archive.php`) — `--query`, `--mailbox`, `--limit`; MySQL FULLTEXT over subject/from/to/cc/body

6. **End-to-end QA** (`web/scripts/qa-archive.sh` / `docker/qa-archive-docker.sh`) — 8 steps using local fixture data, no server needed

---

## Key Files

```
src/maildir_report/
  parser.py              # +cc_addrs, +body_text extraction
  index_mailbox.py       # schema v2, migration, WAL checkpoint
  extract_attachments.py # MIME attachment extraction (unchanged)

web/migrations/
  001_archive_schema.sql # archive_emails + archive_attachments (FULLTEXT)

web/src/cli/
  import_archive.php     # SQLite → MySQL importer
  search_archive.php     # MySQL FULLTEXT search CLI
  migrate.php            # DB migration runner (unchanged)

web/scripts/
  qa-archive.sh          # devenv-based QA
docker/
  qa-archive-docker.sh   # Docker-based QA (no devenv)
  README-windows.md      # Windows quickstart

tests/
  test_body_cc_extraction.py        # 7 parser tests (green)
  test_index_schema_migration_v2.py # 7 migration tests (green)
  fixtures/                          # qa_test_mailbox with 2 fixture emails

devenv.nix               # sync-all, extract-attachments, search-archive scripts
Dockerfile               # Python 3.11 + PHP + MariaDB client
docker-compose.yml       # db (MariaDB 10.11) + app service
```

---

## How to Run (Linux/devenv)

```bash
cd .worktrees/mailbox-archive
devenv shell

# Test with fixtures (no server)
sync-all --mailboxes-file tests/fixtures/mailboxes.txt \
         --src-base tests/fixtures/src \
         --skip-import

# Real sync (requires SSH key auth to thehost.com.ua)
sync-all --mailboxes-file data/mailboxes.txt

# Search
search-archive "invoice"
search-archive "alice" --mailbox gabriel.hangel
```

## How to Run (Windows / Docker)

```powershell
docker compose up -d db
docker compose build app
docker compose run --rm app bash docker/qa-archive-docker.sh
docker compose run --rm app php web/src/cli/search_archive.php --query "invoice"
```

---

## What's NOT Done Yet (Future Work)

- **Web UI** under a mrija.org subdomain (Phase 2 — explicitly deferred)
- **Attachment viewer** in the web UI
- **Pagination** in search results
- **Server-side deletion** — intentionally excluded (user deletes via thehost.com.ua panel)
- `data/mailboxes.txt` for production — needs populating with real mailbox names

---

## Merge Checklist

Before merging `feature/mailbox-archive` → `main`:

- [ ] Run `pytest tests/ -q` — must be green (253 tests)
- [ ] Run `bash web/scripts/qa-archive.sh` inside devenv — ALL STEPS PASSED
- [ ] Populate `data/mailboxes.txt` with real mailbox names
- [ ] Copy `web/config/local.php.example` → `web/config/local.php` and configure DB credentials
- [ ] Run `sync-all` against real server (first time takes a while — 8 GB)
- [ ] Verify `search-archive "test"` returns results

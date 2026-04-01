# Mailbox Archive — Download, Index, Search

**Date:** 2026-04-02
**Status:** Approved

## Summary

The mrija.org mail server (thehost.com.ua) is nearly full (≈8 GB of 10 GB used by 10+
mailboxes). The goal is to download every mailbox to local storage, index all emails and
attachments into a searchable local database (MariaDB + SQLite), and then let the user
clear the server mailboxes manually via the hosting control panel.

A web UI under a mrija.org subdomain is a later phase and is explicitly out of scope here.

## Scope Change (from prior plans)

The previous Phase 1 plan (coworker review + decisions workflow) and Phase 2 plan
(weekly sync) are superseded by this simpler archive-first design.

**Removed entirely:**
- Coworker review UI, bulk decisions, export CSV workflow
- `apply_decisions` CLI tool
- PDF report generation
- Manifest generation
- Decisions CSV template
- IMAP ingestion (rsync is the source)
- Pre-store dedup (not needed for a one-shot archive)

**Kept and extended:**
- rsync-based mailbox download (core of `store-mailbox`)
- `extract_attachments.py`
- `index_mailbox.py` (extended with to/cc/body_text)
- MariaDB dev service in devenv
- `migrate.php` runner (extended with archive tables)
- `web/src/Auth/` and `web/src/Download/DownloadService.php` (kept for later web UI)

## Architecture

```
sync-all (devenv command)
  │
  ├─ for each mailbox in mailboxes.txt:
  │    1. rsync (read-only) → data/mailboxes/<mailbox>/maildir/.maildir/
  │    2. extract_attachments.py → data/mailboxes/<mailbox>/attachments/
  │    3. index_mailbox.py → data/mailboxes/<mailbox>/index.sqlite
  │                        → data/index/mail_index.sqlite  (global merge)
  │
  └─ import_archive.php
       reads  data/index/mail_index.sqlite
       upserts → MariaDB: archive_emails, archive_attachments

search-archive (devenv command)
  └─ queries MariaDB FULLTEXT → prints results to terminal
```

The tool is **strictly read-only** against the server. No messages are moved, flagged,
or deleted. Server cleanup is performed manually by the user via thehost.com.ua panel.

## Data Flow in Detail

### 1. rsync

Same flags as existing `store-mailbox`. Source:
`mrija_org@s16.thehost.com.ua:email/mrija.org/<mailbox>/.maildir/`

Destination: `$DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir/`

### 2. Attachment Extraction

`python3 -m maildir_report.extract_attachments <maildir> <attachments_dir>`

Files written as `<sha256>_<size>.<ext>`. Idempotent — existing identical files are
skipped. Each mailbox has its own `attachments/` directory; no cross-mailbox sharing.

### 3. SQLite Indexing

`python3 -m maildir_report.index_mailbox --mailbox <name> --data-root <path>
  --global-index $DEVENV_ROOT/data/index/mail_index.sqlite`

Per-mailbox `index.sqlite` and global `mail_index.sqlite` are both written. The global
file is the import source for MariaDB.

### 4. MySQL Import

`php web/src/cli/import_archive.php
  --sqlite $DEVENV_ROOT/data/index/mail_index.sqlite`

Upserts all rows into `archive_emails` and `archive_attachments`. Chunked commits
(5 000 rows). Idempotent — re-running is safe.

## Disk Layout

```
$DEVENV_ROOT/
  data/
    mailboxes.txt                        ← one mailbox name per line; # comments ok
    mailboxes/
      <mailbox>/
        maildir/
          .maildir/                      ← rsync target (raw Maildir files)
        attachments/                     ← <sha256>_<size>.<ext>
        index.sqlite                     ← per-mailbox SQLite index
    index/
      mail_index.sqlite                  ← global SQLite (all mailboxes merged)
  web/
    migrations/
      001_archive_schema.sql             ← replaces old 001; archive tables only
    src/
      cli/
        migrate.php                      ← existing runner (unchanged)
        import_archive.php               ← NEW
      Auth/                              ← kept for later web UI
      Download/DownloadService.php       ← kept for later web UI
```

## Database Schema (MariaDB)

### `archive_emails`

```sql
CREATE TABLE archive_emails (
  mailbox          VARCHAR(255) NOT NULL,
  stable_id        CHAR(64)     NOT NULL,
  filepath         TEXT         NOT NULL,
  folder           VARCHAR(255) NOT NULL DEFAULT '',
  date             VARCHAR(64)  NOT NULL DEFAULT '',
  from_addr        VARCHAR(255) NOT NULL DEFAULT '',
  to_addrs         TEXT         NOT NULL DEFAULT '',
  cc_addrs         TEXT         NOT NULL DEFAULT '',
  subject          TEXT         NOT NULL DEFAULT '',
  body_text        LONGTEXT     NOT NULL DEFAULT '',
  total_size_bytes BIGINT       NOT NULL DEFAULT 0,
  imported_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (mailbox, stable_id),
  KEY idx_date (mailbox, date),
  FULLTEXT KEY ftx_email (subject, from_addr, to_addrs, cc_addrs, body_text)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### `archive_attachments`

```sql
CREATE TABLE archive_attachments (
  mailbox          VARCHAR(255) NOT NULL,
  email_stable_id  CHAR(64)     NOT NULL,
  stored_path      TEXT         NOT NULL,
  sha256           CHAR(64)     NOT NULL,
  size             BIGINT       NOT NULL DEFAULT 0,
  mime             VARCHAR(255) NOT NULL DEFAULT '',
  original_filename TEXT        NOT NULL DEFAULT '',
  imported_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (mailbox, email_stable_id, sha256),
  KEY idx_email (mailbox, email_stable_id),
  KEY idx_sha256 (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

## Parser Extension (`parser.py`)

Three new fields added to the email record dict:

| Field | Source | Fallback |
|-------|--------|---------|
| `to_addrs` | Raw `To:` header | `""` |
| `cc_addrs` | Raw `Cc:` header | `""` |
| `body_text` | First `text/plain` part decoded; fallback: HTML tags stripped | `""` |

Implementation uses `msg.walk()` (compat32 policy preserved). Charset decoding uses
declared charset → `utf-8` → `latin-1` with `errors='replace'`. No change to
`stable_id` generation.

## SQLite Schema Upgrade (`index_mailbox.py`)

Three new columns on the `emails` table: `to_addrs TEXT NOT NULL DEFAULT ''`,
`cc_addrs TEXT NOT NULL DEFAULT ''`, `body_text TEXT NOT NULL DEFAULT ''`.

Migration via `PRAGMA user_version`: v1 → v2 uses `ALTER TABLE … ADD COLUMN`.
Existing databases are migrated in-place. Per-mailbox and global DBs are both migrated.
WAL checkpoint runs after bulk indexing to prevent large WAL files.

## Search Interface

`search-archive` devenv command — queries MariaDB FULLTEXT, prints to terminal:

```bash
search-archive "invoice"
search-archive "invoice" --mailbox gabriel.hangel
search-archive "invoice" --limit 20
```

Output per result:
```
[gabriel.hangel] 2024-03-15  From: supplier@x.com  Subject: Invoice March
  To: gabriel@mrija.org
  Attachments: invoice_march.pdf (42 KB)
  File: data/mailboxes/gabriel.hangel/maildir/.maildir/cur/1234.eml
```

## Mailbox List File

`data/mailboxes.txt` — one name per line, `#` comments, blank lines ignored.
Names validated against `^[A-Za-z0-9._-]+$` (no path traversal possible).

Default remote fetch: `mrija_org@s16.thehost.com.ua:email/mrija.org/mailboxes.txt`
Override: `--mailboxes-file <local-path>` (for testing without server access).

## `sync-all` Command Options

```bash
sync-all                              # full run against all mailboxes
sync-all --mailboxes-file local.txt   # use local list (testing)
sync-all --skip-import                # download+index only, skip MySQL step
sync-all --mailbox gabriel.hangel     # single mailbox (incremental re-sync)
```

Behaviour on failure: logs the error, continues with remaining mailboxes, exits
non-zero at the end if any mailbox failed.

## Cleanup — Files to Remove

Everything below is deleted as part of this spec. Tests covering removed modules are
also deleted.

### Python modules
| File | Reason |
|------|--------|
| `src/maildir_report/apply_decisions.py` | Decision workflow removed |
| `src/maildir_report/decisions_template.py` | No more decisions CSV |
| `src/maildir_report/pdf.py` | No more PDF reports |
| `src/maildir_report/manifest.py` | No more manifest |
| `src/maildir_report/imap_ingest.py` | rsync is the only source |
| `src/maildir_report/pre_store_dedup.py` | Not needed for archive |
| `src/maildir_report/cli.py` | Replaced by sync-all devenv script |
| `src/maildir_report/__main__.py` | Entry point for removed pipeline |

### Tests
| File | Reason |
|------|--------|
| `tests/test_apply_decisions.py` | Module removed |
| `tests/test_decisions_template.py` | Module removed |
| `tests/test_pdf_german_headers.py` | Module removed |
| `tests/test_imap_ingest.py` | Module removed |
| `tests/test_pre_store_dedup.py` | Module removed |
| `tests/test_e2e_cli.py` | CLI pipeline removed |

### Web app
| File | Reason |
|------|--------|
| `web/src/Services/ReviewService.php` | Review workflow removed |
| `web/src/Import/` | Import of decisions CSV removed |
| `web/public/login.php` | No web UI yet |
| `web/scripts/qa-task5-*.sh` | Auth QA for removed review app |
| `web/scripts/qa-task6-*.sh` | Review UI QA removed |
| `web/migrations/001_initial_schema.sql` | Replaced by new `001_archive_schema.sql` — fresh DB with archive tables only |
| `web/public/index.php` | Full of review routes; removed now, rebuilt for web UI phase |

### devenv scripts (in `devenv.nix`)
| Script | Reason |
|--------|--------|
| `scan-mailbox` | Replaced by `sync-all` |
| `fetch-imap` | IMAP ingestion removed |
| `review-start` | No review web app |
| `apply-decisions` | Decision workflow removed |
| `store-mailbox` | Replaced by `sync-all` internals |

### Kept devenv scripts
`db-start`, `db-migrate`, `index-mailbox`, `index-all`
(plus new: `sync-all`, `extract-attachments`, `search-archive`)

## Testing Phase

Every step is verified locally with synthetic fixture data before touching the real
server. Tests run without network access and without a real IMAP/rsync connection.

### Fixture setup

A small synthetic mailbox is created under `tests/fixtures/mailboxes/test_mailbox/`
containing:
- 3 plain-text emails
- 1 email with an attachment (PDF fixture)
- 1 email with a duplicate of an earlier email (same bytes)
- A matching `mailboxes.txt` listing only `test_mailbox`

### Per-step verification

| Step | How tested | Tool |
|------|-----------|------|
| rsync | `sync-all` accepts `--src-base <local-path>` override to rsync from a local fixture dir instead of the server | `pytest` + devenv QA script |
| Attachment extraction | `extract_attachments.py` runs on fixture maildir; assert files present under `attachments/` with correct sha256 names | `pytest` |
| SQLite indexing | `index_mailbox.py` runs on fixture; assert row count, column values, `to_addrs`/`cc_addrs`/`body_text` populated | `pytest` |
| Schema migration | Create an old-schema SQLite (v1), run indexer, assert new columns exist | `pytest` |
| MySQL import | `import_archive.php` runs against fixture global SQLite; assert row counts in `archive_emails` and `archive_attachments` | devenv QA script (`qa-archive.sh`) |
| search-archive | Query for a known keyword from fixture body; assert result appears | devenv QA script |
| Full pipeline | `sync-all --mailboxes-file tests/fixtures/mailboxes.txt --src-base tests/fixtures` exits 0 and populates DB | devenv QA script |

### QA script

`web/scripts/qa-archive.sh` orchestrates the full local test:
1. Start MariaDB (`db-start`)
2. Run migrations (`db-migrate`)
3. Run `sync-all` against fixture data (no server)
4. Assert MySQL row counts match expected
5. Assert `search-archive` returns a known result
6. Exit 0 only if all assertions pass

All tests pass locally before `sync-all` is run against the real server.

## Not in Scope (this phase)

- Web UI / subdomain (later)
- Attachment content search (PDF/DOCX text extraction)
- IMAP-based download
- Any server-side deletion
- Email sending / SMTP

## Success Criteria

- `sync-all --mailboxes-file fixtures/test-mailboxes.txt --skip-import` exits 0 and
  produces correct folder layout for a fixture mailbox.
- `sync-all --mailboxes-file fixtures/test-mailboxes.txt` exits 0 and populates
  `archive_emails` rows in MariaDB.
- `search-archive "test"` returns at least one result from the fixture mailbox.
- `pytest -q` exits 0 (all remaining tests pass).
- Removed files are gone from the repo.

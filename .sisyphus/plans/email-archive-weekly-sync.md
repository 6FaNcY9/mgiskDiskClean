# Weekly Maildir Archive + MySQL Search (All Mailboxes)

## TL;DR
> **Summary**: Add a weekly cron-driven sync that rsyncs all Maildir mailboxes, extracts/dedupes attachments, builds per-mailbox + global SQLite indexes (including to/cc/body text), imports the global index into MySQL archive tables (FULLTEXT over email body), and exposes a PHP search UI with authenticated download links for raw emails and attachments.
> **Deliverables**:
> - Weekly CLI (`devenv run sync-weekly`) driven by remote mailbox list file
> - Attachment extraction wrapper (`devenv run extract-attachments`)
> - SQLite schema upgrade: `emails` gains `to_addrs`, `cc_addrs`, `body_text` (+ safe in-place migrations)
> - MySQL schema: new archive tables + FULLTEXT index (server “databank” compatible)
> - Global search UI (`GET /archive/search`) querying MySQL (not SQLite)
> - Download endpoints for raw email + attachment (not report-scoped)
> **Effort**: Large
> **Parallel**: YES — 3 waves + final verification
> **Critical Path**: Parser body/CC extraction → SQLite schema+migration → MySQL archive schema + importer → weekly sync wrapper → PHP search + downloads → QA

## Context
### Original Request
- Boss changed scope: remove/scrap decision creation + deletion ideas.
- Build a weekly system that downloads *all mailboxes* (emails + attachments), keeps a local archive, and maintains a DB index with:
  - email metadata (from/to/cc/date/subject/body text)
  - attachment metadata
  - physical filesystem paths for later downloads (raw email + attachment)
- Use rsync Maildir as source; cron runs CLI weekly; no extra compression; full-text search on email body (no attachment OCR).

### Interview Summary
- Email source: Rsync Maildir (reuse existing pipeline).
- Weekly trigger: server cron runs CLI.
- Compression: none (keep raw; rely on current content-addressable/dedup layout).
- Disk layout: keep `data/mailboxes/<mailbox>/{maildir/.maildir,attachments,reports,index.sqlite}`.
- Mailbox discovery: **remote list file**.
- Cron runtime: **devenv/nix** (cron runs `devenv run …`).
- Search UI scope: **all mailboxes**.
- Body storage: **full body text** stored in SQLite and imported into MySQL for FULLTEXT search.

### Metis Review (gaps addressed)
- Confirmed: `devenv.nix` `store-mailbox` does rsync + optional pre_store_dedup + report pipeline only; it does **not** run attachment extraction or indexing.
- Parser uses compat32 policy; implement body extraction via `msg.walk()` (do NOT switch policies).
- SQLite schema must be migrated (existing DBs): use `PRAGMA user_version` + `ALTER TABLE` safely.
- Existing downloads are report-scoped; global search needs mailbox-scoped download routes with strict path guards.

## Work Objectives
### Core Objective
Deliver a weekly, fully automated ingestion + indexing flow that produces a searchable archive of all mailboxes (including body full-text search) and provides authenticated downloads for raw email + attachments.

### Deliverables
1. Weekly sync command (cron-friendly): rsync all mailboxes → extract attachments → index mailboxes → update global index.
2. Upgraded per-mailbox + global SQLite schema including body/cc (ingestion artifact).
3. MySQL archive tables populated weekly from the global SQLite index.
4. PHP search page querying MySQL archive tables.
5. Authenticated download endpoints for:
   - raw email (.eml) by mailbox + stable_id
   - attachment by mailbox + stable_id + sha256
6. Agent-executed QA scripts (no “manual testing required”).

### Definition of Done (agent-verifiable)
- [ ] `devenv run sync-weekly --help` exits 0 and documents required env/config.
- [ ] With a synthetic mailbox in `data/mailboxes/test/…`, running `devenv run sync-weekly --mailboxes-file <localfile> --src-base <rsync-base>` completes with exit 0 and produces:
  - `data/mailboxes/test/attachments/*` extracted files
  - `data/mailboxes/test/index.sqlite` with upgraded schema (`to_addrs`, `cc_addrs`, `body_text`)
  - `data/index/mail_index.sqlite` global index with the same upgraded schema
- [ ] `pytest -q` passes.
- [ ] PHP QA: `bash web/scripts/qa-archive-search.sh` passes (starts php -S, hits `/archive/search`, validates results include expected stable_id and download links).

### Must Have
- Keep existing folder layout under `data/mailboxes/<mailbox>/`.
- Never modify remote mailboxes (rsync read-only; no apply/delete).
- Index includes email: from/to/cc/date/subject/body_text + filepath.
- Full-text search over body_text (plus subject/from/to/cc) via MySQL FULLTEXT.
- Global search across all mailboxes.
- Download raw email + attachments via authenticated routes with strict traversal guards.

### Must NOT Have (guardrails)
- Do NOT add any deletion / decision apply / quarantine workflows.
- Do NOT add attachment OCR or content extraction.
- Do NOT change existing report/review tables (`reports`, `emails`, `decisions`) except by ADDING new archive tables/migrations.
- Do NOT switch parser to `policy.default` / `EmailMessage.get_body()`.
- Do NOT accept user-supplied file paths; all downloads must resolve from **MySQL archive tables** and then be validated against guarded roots under `data_dir`.

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: **TDD** for new parser extraction + SQLite schema migration + MySQL archive migration + importer + PHP search/download routes.
- Evidence files (executor must write per task): `.sisyphus/evidence/task-{N}-{slug}.txt` (command outputs, key query results, curl responses).

## Execution Strategy
### Parallel Execution Waves

Wave 1 (foundations / red tests / wrappers)
- Task 1–6

Wave 2 (Python parsing + SQLite schema migration + MySQL importer)
- Task 7–11

Wave 3 (PHP global search + downloads + QA scripts)
- Task 12–14

### Dependency Matrix (high level)
- 7 (parser CC/body) → 8/9 (SQLite schema+migration) → 2/11 (MySQL schema + importer) → 12/13 (search UI + downloads) → 14 (end-to-end QA)
- 3/4 (mailbox list + extract wrapper) block 6 (sync-weekly)
- 2/4/11–13 block 14 (end-to-end QA)

## TODOs

- [ ] 1. Fix PHP parse error in `ReviewService.php`

  **What to do**:
  - Inspect and fix the extra closing brace in `web/src/Services/ReviewService.php` (currently shows an extra `}` after `getReviewers()`), ensuring the file parses and is autoloadable.
  - Add a minimal QA check: `php -l web/src/Services/ReviewService.php`.

  **Must NOT do**: Any functional changes to decision/review workflows.

  **Recommended Agent Profile**:
  - Category: `quick`
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 12–14 | Blocked By: none

  **References**:
  - Bug location: `web/src/Services/ReviewService.php:156-163` (extra `}`)

  **Acceptance Criteria**:
  - [ ] `php -l web/src/Services/ReviewService.php` → “No syntax errors detected”

  **QA Scenarios**:
  ```
  Scenario: Syntax check
    Tool: Bash
    Steps: php -l web/src/Services/ReviewService.php
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-1-php-parse-fix.txt
  ```

  **Commit**: YES | Message: `fix(web): repair ReviewService syntax`

### NOTE (scope change)
This plan was updated to make **MySQL the primary search/index DB** for the archive (per user request). All prior SQLite-FTS-only steps were removed; SQLite remains the ingestion/index artifact written by the Python pipeline.

- [ ] 2. Add MySQL archive schema migration (new tables + FULLTEXT)

  **What to do**:
  - Add a new migration file: `web/migrations/002_archive_schema.sql` (next number if 002 already exists).
  - Create new tables (do NOT reuse the report-scoped `emails` table):
    - `archive_emails` (PK: `(mailbox, stable_id)`)
      - `mailbox VARCHAR(255) NOT NULL`
      - `stable_id CHAR(64) NOT NULL`
      - `filepath TEXT NOT NULL`
      - `folder VARCHAR(255) NOT NULL DEFAULT ''`
      - `date VARCHAR(64) NOT NULL DEFAULT ''`
      - `from_addr VARCHAR(255) NOT NULL DEFAULT ''`
      - `to_addrs TEXT NOT NULL DEFAULT ''`
      - `cc_addrs TEXT NOT NULL DEFAULT ''`
      - `subject TEXT NOT NULL DEFAULT ''`
      - `body_text LONGTEXT NOT NULL DEFAULT ''`
      - `total_size_bytes BIGINT NOT NULL DEFAULT 0`
      - `imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`
      - `KEY idx_archive_emails_date (mailbox, date)`
      - `FULLTEXT KEY ftx_archive_emails (subject, from_addr, to_addrs, cc_addrs, body_text)`
    - `archive_attachments` (PK: `(mailbox, email_stable_id, stored_path)`)
      - `mailbox VARCHAR(255) NOT NULL`
      - `email_stable_id CHAR(64) NOT NULL`
      - `sha256 CHAR(64) NOT NULL`
      - `size BIGINT NOT NULL DEFAULT 0`
      - `mime VARCHAR(255) NOT NULL DEFAULT ''`
      - `original_filename TEXT NOT NULL DEFAULT ''`
      - `stored_path TEXT NOT NULL`
      - `imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`
      - `KEY idx_archive_attachments_email (mailbox, email_stable_id)`
      - `KEY idx_archive_attachments_sha256 (sha256)`

  **Must NOT do**:
  - Do not modify `reports`, `emails`, `decisions` schemas.

  **Recommended Agent Profile**:
  - Category: `quick`
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 11,12,13,14 | Blocked By: none

  **References**:
  - Existing migration style: `web/migrations/001_initial_schema.sql:6-50`
  - Migration runner: `web/src/cli/migrate.php:78-123`

  **Acceptance Criteria**:
  - [ ] `devenv run db-start` exits 0
  - [ ] `devenv run db-migrate` applies the new migration successfully

  **QA Scenarios**:
  ```
  Scenario: Apply migrations
    Tool: Bash
    Steps:
      - devenv run db-start
      - devenv run db-migrate
    Expected: exit 0; migration runner prints [apply] for the new file
    Evidence: .sisyphus/evidence/task-2-mysql-archive-migration.txt
  ```

  **Commit**: YES | Message: `feat(db): add MySQL archive tables for mailbox archive`

- [ ] 3. Define mailbox list file format + remote fetch behavior

  **What to do**:
  - Decide and document the mailbox list file contract used by cron:
    - Remote path (DEFAULT): `s16.thehost.com.ua:email/mrija.org/mailboxes.txt`
    - Format: one mailbox per line; blank lines allowed; `#` comments allowed.
  - Add parsing logic for this format in the weekly sync wrapper (see Task 6).
  - Add guardrails: mailbox names must match allowlist regex `^[A-Za-z0-9._-]+$`.

  **Must NOT do**: Auto-discover mailboxes by listing remote directories (explicitly chose list file).

  **Recommended Agent Profile**:
  - Category: `quick`
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 6 | Blocked By: none

  **References**:
  - Mailbox name validation pattern: `web/src/Download/DownloadService.php:51-53`
  - Existing mailbox layout root: `devenv.nix:161-165` and `src/maildir_report/index_mailbox.py:270-276`

  **Acceptance Criteria**:
  - [ ] A local sample file with comments parses into the expected mailbox list in a unit test or shell QA.

  **QA Scenarios**:
  ```
  Scenario: Parse mailbox list file
    Tool: Bash
    Steps: pytest -q -k mailbox_list
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-3-mailbox-list.txt
  ```

  **Commit**: YES | Message: `docs(sync): define mailbox list file contract`

- [ ] 4. Add `devenv` command wrapper: `extract-attachments <mailbox>`

  **What to do**:
  - Extend `devenv.nix` with a new command `extract-attachments`:
    - Input: `<mailbox>`
    - Reads: `$DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir/`
    - Writes: `$DEVENV_ROOT/data/mailboxes/<mailbox>/attachments/`
    - Runs: `PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments <maildir> <attachments>`
  - Ensure help text exists and exit code behavior is consistent with `store-mailbox` / `index-mailbox`.

  **Must NOT do**: Modify remote mailboxes; this is strictly local extraction.

  **Recommended Agent Profile**:
  - Category: `quick`
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 6 | Blocked By: none

  **References**:
  - Maildir root: `devenv.nix:162` and `src/maildir_report/index_mailbox.py:292-294`
  - Extract CLI: `src/maildir_report/extract_attachments.py:50-52`

  **Acceptance Criteria**:
  - [ ] `devenv run extract-attachments --help` exits 0

  **QA Scenarios**:
  ```
  Scenario: Wrapper help
    Tool: Bash
    Steps: devenv run extract-attachments --help
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-4-extract-wrapper-help.txt
  ```

  **Commit**: YES | Message: `feat(devenv): add extract-attachments command`

- [ ] 5. Add red tests for CC + body extraction in parser

  **What to do**:
  - Add new pytest file `tests/test_body_cc_extraction.py` with fixtures similar to `tests/test_task2b_attachments_index.py`:
    - Email with `Cc: Alice <a@x>, Bob <b@y>` should produce `email_rec['cc']` exact header string.
    - Plain-text body should populate `email_rec['body_text']` with decoded text.
    - HTML-only body should still populate body_text (basic html→text conversion is OK; assert it contains key words).
    - Charset edge: windows-1251 body should decode with replacement fallback.
    - No body part: body_text becomes empty string.

  **Must NOT do**: Change existing test assertions; only add new tests.

  **Recommended Agent Profile**:
  - Category: `deep`
  - Skills: [`test-driven-development`]

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 7 | Blocked By: none

  **References**:
  - Parser return keys: `src/maildir_report/parser.py` (EmailRecord dict)
  - Fixture builders: `tests/test_task2b_attachments_index.py:54-123`

  **Acceptance Criteria**:
  - [ ] `pytest -q tests/test_body_cc_extraction.py` fails (red) for missing keys/behavior prior to implementation.

  **QA Scenarios**:
  ```
  Scenario: Red tests exist
    Tool: Bash
    Steps: pytest -q tests/test_body_cc_extraction.py
    Expected: exit != 0 with assertion(s) about missing/empty cc/body_text
    Evidence: .sisyphus/evidence/task-5-red-parser-tests.txt
  ```

  **Commit**: YES | Message: `test(parser): add red tests for cc/body extraction`

- [ ] 6. Add weekly sync command wrapper: `devenv run sync-weekly`

  **What to do**:
  - Extend `devenv.nix` with a new command `sync-weekly` that:
    1) fetches the remote mailbox list file (default remote path per Task 3) via rsync/ssh
    2) loops mailboxes and, for each mailbox:
       - rsync Maildir into `data/mailboxes/<mailbox>/maildir/.maildir/` (same rsync flags as store-mailbox)
       - run `extract-attachments <mailbox>`
       - run `index-mailbox <mailbox>` with `--global-index $DEVENV_ROOT/data/index/mail_index.sqlite`
       - rely on `index-mailbox` to run SQLite WAL checkpoint after bulk indexing (Task 10)
    3) after all mailboxes are indexed, import the updated global SQLite into MySQL:
       - run: `php web/src/cli/import_archive.php --sqlite "$DEVENV_ROOT/data/index/mail_index.sqlite"`
    4) exits non-zero if any mailbox fails (but prints which mailbox)
  - Add options:
    - `--mailboxes-file <path>` to use a local file (for QA)
    - `--src-base <rsync-base>` override for rsync base path
      - Default: `mrija_org@s16.thehost.com.ua:email/mrija.org` (same as `store-mailbox`)
    - `--skip-import` to skip the MySQL import step (dev-only; cron must NOT use this)
  - Ensure it is cron-friendly (no prompts; clear logs).

  **Must NOT do**:
  - Do not call `python -m maildir_report` report pipeline (PDF/manifest/decisions) as part of weekly sync; keep weekly sync focused on archive+index.

  **Recommended Agent Profile**:
  - Category: `unspecified-high`
  - Skills: []

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 14 | Blocked By: 3,4

  **References**:
  - Existing rsync + layout patterns: `devenv.nix:126-208` (store-mailbox)
  - Index command: `devenv.nix:210-241` (index-mailbox)
  - Global index path convention: `devenv.nix:259-272`

  **Acceptance Criteria**:
  - [ ] `devenv run sync-weekly --help` exits 0
  - [ ] Using a local mailbox list file: `sync-weekly --skip-import` can run end-to-end against a synthetic local mailbox (no remote), at least for the extract+index steps.

  **QA Scenarios**:
  ```
  Scenario: Wrapper help
    Tool: Bash
    Steps: devenv run sync-weekly --help
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-6-sync-weekly-help.txt
  ```

  **Commit**: YES | Message: `feat(devenv): add sync-weekly (rsync+extract+index+import)`

- [ ] 7. Implement CC + body extraction in `src/maildir_report/parser.py`

  **What to do**:
  - Extend `parse_email_file()` / record builder to include:
    - `cc`: raw CC header string (empty if missing)
    - `body_text`: extracted full body text (prefer text/plain; fallback to html→text)
  - Must use compat32-safe logic:
    - Walk parts via `msg.walk()`
    - Decode payload with declared charset; fallback `utf-8` then `latin-1` with `errors='replace'`
  - Keep existing `parts` attachment logic unchanged (body parts currently excluded from `parts`; keep it that way).

  **Must NOT do**:
  - Do not change stable_id generation semantics.
  - Do not switch email parsing policy.

  **Recommended Agent Profile**:
  - Category: `deep`
  - Skills: [`test-driven-development`]

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 8,9 | Blocked By: 5

  **References**:
  - Body-part exclusion today (do not regress attachments): `src/maildir_report/parser.py` body mime filtering
  - Metis guardrail: compat32 — must use `walk()`

  **Acceptance Criteria**:
  - [ ] `pytest -q tests/test_body_cc_extraction.py` passes
  - [ ] `pytest -q` full suite passes

  **QA Scenarios**:
  ```
  Scenario: Parser extracts cc + body_text
    Tool: Bash
    Steps: pytest -q tests/test_body_cc_extraction.py
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-7-parser-cc-body.txt
  ```

  **Commit**: YES | Message: `feat(parser): extract cc and body_text`

- [ ] 8. Add red tests for SQLite schema migration (new columns)

  **What to do**:
  - Add a new pytest file `tests/test_index_schema_migration_v2.py` that:
    - creates an “old schema” index.sqlite (v1) with only the current columns
    - runs `_init_db()` (or a public helper) and asserts new columns exist
    - asserts both per-mailbox and global index DBs get the new columns

  **Must NOT do**: Require external sqlite3 CLI; use Python sqlite3 for tests.

  **Recommended Agent Profile**:
  - Category: `deep`
  - Skills: [`test-driven-development`]

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 9 | Blocked By: none

  **References**:
  - Current schema DDL: `src/maildir_report/index_mailbox.py:95-127`
  - Indexing behavior: `INSERT OR REPLACE` upserts: `src/maildir_report/index_mailbox.py:192-208`

  **Acceptance Criteria**:
  - [ ] `pytest -q tests/test_index_schema_migration_v2.py` fails (red) before implementation

  **QA Scenarios**:
  ```
  Scenario: Red schema migration tests
    Tool: Bash
    Steps: pytest -q tests/test_index_schema_migration_v2.py
    Expected: exit != 0
    Evidence: .sisyphus/evidence/task-8-red-schema-migration-tests.txt
  ```

  **Commit**: YES | Message: `test(index): add red tests for schema migration (body/cc)`

- [ ] 9. Upgrade `index_mailbox.py` schema: new columns + migrations

  **What to do**:
  - Extend `emails` table to include (TEXT NOT NULL DEFAULT ''):
    - `to_addrs`
    - `cc_addrs`
    - `body_text`
  - Implement migrations using `PRAGMA user_version`:
    - v1 → v2: add new columns (ALTER TABLE)
  - (No SQLite FTS in this MySQL-primary plan.)
  - Update `_upsert_email()` to write `to_addrs`, `cc_addrs`, `body_text` from parser output keys (use empty string fallback).
  - Ensure both per-mailbox and global index DBs are migrated in-place.
  - Ensure `index_mailbox(..., global_index_path=…)` applies the same schema/migrations to the global DB.

  **Must NOT do**:
  - Do not drop/rebuild DBs; must be in-place migration.
  - Do not add MySQL changes.

  **Recommended Agent Profile**:
  - Category: `unspecified-high`
  - Skills: [`test-driven-development`]

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 11–14 | Blocked By: 7,8

  **References**:
  - Index DB initializer: `src/maildir_report/index_mailbox.py:_init_db`
  - Global index writes: `src/maildir_report/index_mailbox.py:308-346`

  **Acceptance Criteria**:
  - [ ] `pytest -q tests/test_index_schema_migration_v2.py` passes
  - [ ] Existing tests in `tests/test_task2b_attachments_index.py` still pass

  **QA Scenarios**:
  ```
  Scenario: Schema migration green
    Tool: Bash
    Steps: pytest -q tests/test_index_schema_migration_v2.py
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-9-schema-migration-green.txt
  ```

  **Commit**: YES | Message: `feat(index): migrate emails schema for to/cc/body_text`

- [ ] 10. Add WAL checkpoint after indexing runs

  **What to do**:
  - After bulk indexing in `index_mailbox()`, run `PRAGMA wal_checkpoint(TRUNCATE)` (for both per-mailbox and global connections) to prevent large lingering WAL files during weekly cron.
  - Add/adjust tests if needed to ensure the pragma doesn’t break.

  **Recommended Agent Profile**:
  - Category: `quick`
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 6,14 | Blocked By: 9

  **References**:
  - Connections close in finally: `src/maildir_report/index_mailbox.py:343-346`

  **Acceptance Criteria**:
  - [ ] `pytest -q` passes

  **QA Scenarios**:
  ```
  Scenario: Indexing still works after checkpoint
    Tool: Bash
    Steps: pytest -q tests/test_task2b_attachments_index.py
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-10-wal-checkpoint.txt
  ```

  **Commit**: YES | Message: `chore(sqlite): checkpoint WAL after indexing`

- [ ] 11. Add archive importer CLI: global SQLite → MySQL archive tables

  **What to do**:
  - Add `web/src/cli/import_archive.php` (CLI-only) that:
    - loads config like `web/src/cli/migrate.php` (default config: `web/config/local.php`)
    - opens global SQLite: `<data_dir>/index/mail_index.sqlite` (read-only)
    - upserts into MySQL:
      - `archive_emails` from SQLite `emails`
      - `archive_attachments` from SQLite `attachments`
    - uses prepared statements + chunked commits (e.g. commit every 5k rows)
    - is idempotent: re-running does not create duplicates
  - Add `--help`, `--config <path>`, `--sqlite <path>` overrides.

  **Must NOT do**:
  - Do not read Maildir directly (source of truth for import is the SQLite index file).

  **Recommended Agent Profile**:
  - Category: `unspecified-high`
  - Skills: []

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 12,13,14 | Blocked By: 2,9

  **References**:
  - Config + PDO pattern: `web/src/cli/migrate.php:41-76`
  - Global index schema: `src/maildir_report/index_mailbox.py:_CREATE_EMAILS` and `:_CREATE_ATTACHMENTS`
  - Global index path convention: `devenv.nix:243-272`

  **Acceptance Criteria**:
  - [ ] `php web/src/cli/import_archive.php --help` exits 0
  - [ ] On a synthetic mailbox index, running importer results in non-zero rows in `archive_emails`.

  **QA Scenarios**:
  ```
  Scenario: Importer help
    Tool: Bash
    Steps: php web/src/cli/import_archive.php --help
    Expected: exit 0
    Evidence: .sisyphus/evidence/task-11-importer-help.txt
  ```

  **Commit**: YES | Message: `feat(cli): import archive index into MySQL`

- [ ] 12. Implement global archive search route + service (PHP, MySQL FULLTEXT)

  **What to do**:
  - Add a new authenticated route in `web/public/index.php`:
    - `GET /archive/search` with query params: `q`, optional `mailbox`, paging (`page`, `page_size`)
  - Implement `web/src/Services/ArchiveSearchService.php` (new) that:
    - queries `archive_emails` in MySQL using `MATCH(...) AGAINST (...)`
    - supports optional mailbox filter + paging
    - returns enough fields to render: mailbox, date, from/to/cc, subject, body preview
  - Render results including:
    - mailbox, date, from, to/cc, subject
    - body preview (server-side): `LEFT(body_text, 400)` (and escape for HTML output)
    - download links (Task 13 endpoints)

  **Must NOT do**:
  - Do not depend on report imports (this is separate archive search).
  - Do not expose raw filesystem paths in HTML.

  **Recommended Agent Profile**:
  - Category: `visual-engineering`
  - Skills: [`frontend-ui-ux`]

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 14 | Blocked By: 11,2,1

  **References**:
  - Router style: `web/public/index.php` download routes around `:854+` and `:895+`
  - PDO factory pattern: `web/public/index.php:111-127`
  - Existing LIKE-search pattern: `web/src/Services/ReviewService.php:70-73`
  - Data root conventions: `web/config/*.php` (dataDir)
  - Global index path: `devenv.nix:259-272` (`data/index/mail_index.sqlite`)

  **Acceptance Criteria**:
  - [ ] `bash web/scripts/qa-archive-search.sh` passes (added in Task 14)

  **QA Scenarios**:
  ```
  Scenario: Search endpoint returns results
    Tool: Bash
    Steps: bash web/scripts/qa-archive-search.sh
    Expected: exit 0; response contains stable_id + mailbox
    Evidence: .sisyphus/evidence/task-12-archive-search.txt
  ```

  **Commit**: YES | Message: `feat(web): add archive global search (MySQL FULLTEXT)`

- [ ] 13. Add mailbox-scoped raw email + attachment download endpoints (PHP, MySQL-backed)

  **What to do**:
  - Add new routes:
    - `GET /download/archive/email/{mailbox}/{stable_id}`
    - `GET /download/archive/attachment/{mailbox}/{stable_id}/{sha256}`
  - Extend `DownloadService` with:
    - `resolveArchiveEmail(mailbox, stable_id)`:
      - validate mailbox via `MAILBOX_PATTERN` (`DownloadService.php:51-53`)
      - validate `stable_id` as `/^[0-9a-f]{64}$/`
      - query MySQL `archive_emails` for `filepath`
      - assert filepath under `<dataDir>/mailboxes/<mailbox>/maildir/.maildir/`
    - `resolveArchiveAttachment(mailbox, stable_id, sha256)`:
      - validate mailbox + stable_id + sha256
      - query MySQL `archive_attachments` for `stored_path, original_filename, mime`
      - assert stored_path under `<dataDir>/mailboxes/<mailbox>/attachments/`

  **Must NOT do**:
  - Do not accept arbitrary file path.
  - Do not require report_id.

  **Recommended Agent Profile**:
  - Category: `unspecified-high`
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 14 | Blocked By: 11,2

  **References**:
  - Existing path guard + MIME map patterns: `web/src/Download/DownloadService.php:24-35` and `:64-76`
  - Existing report download route pattern: `web/public/index.php:854-935`

  **Acceptance Criteria**:
  - [ ] QA script downloads an `.eml` and an attachment via the new archive routes and returns 200

  **QA Scenarios**:
  ```
  Scenario: Download raw email
    Tool: Bash
    Steps:
      - Start php server (qa script)
      - curl -f -I "http://127.0.0.1:8000/download/archive/email/test_mailbox/<stable_id>"
    Expected: HTTP 200; Content-Type includes message/rfc822 or octet-stream
    Evidence: .sisyphus/evidence/task-13-download-eml.txt

  Scenario: Download attachment
    Tool: Bash
    Steps:
      - Start php server (qa script)
      - curl -f -I "http://127.0.0.1:8000/download/archive/attachment/test_mailbox/<stable_id>/<sha256>"
    Expected: HTTP 200; Content-Type matches stored MIME or application/octet-stream
    Evidence: .sisyphus/evidence/task-13-download-attachment.txt
  ```

  **Commit**: YES | Message: `feat(web): add archive download routes (MySQL-backed)`

- [ ] 14. Add agent-executed PHP QA script for archive search + downloads (MySQL)

  **What to do**:
  - Add `web/scripts/qa-archive-search.sh` that:
    1) ensures DB is up + migrated:
       - `devenv run db-start`
       - `devenv run db-migrate`
    2) creates a synthetic mailbox under `$DEVENV_ROOT/data/mailboxes/test_mailbox/…`
       - writes a small Maildir email containing a unique token in body
       - runs `devenv run extract-attachments test_mailbox`
       - runs `devenv run index-mailbox test_mailbox` with global index path
       - runs `php web/src/cli/import_archive.php` (pointed at the global SQLite path)
    3) starts `devenv run review-start --port 8000` in background
    4) curls `/archive/search?q=<token>` and asserts it contains mailbox + stable_id
    5) curls archive download routes for 200
    6) shuts down server

  **Must NOT do**: Require human inspection.

  **Recommended Agent Profile**:
  - Category: `devops`
  - Skills: []

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: F3 | Blocked By: 2,4,11,12,13

  **References**:
  - Local QA server pattern: `devenv.nix:274-311` (`review-start`)
  - Test fixture builders: `tests/test_task2b_attachments_index.py` (Maildir skeleton)

  **Acceptance Criteria**:
  - [ ] `bash web/scripts/qa-archive-search.sh` exits 0

  **QA Scenarios**:
  ```
  Scenario: End-to-end archive search QA
    Tool: Bash
    Steps: bash web/scripts/qa-archive-search.sh
    Expected: exit 0; curl assertions pass
    Evidence: .sisyphus/evidence/task-14-qa-archive-search.txt
  ```

  **Commit**: YES | Message: `test(web): add QA script for archive search`

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> Run in PARALLEL. ALL must approve. Present consolidated results to user and wait for explicit approval.
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA (agent-executed) — unspecified-high (+ playwright only if UI needs browser rendering)
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Keep commits atomic (tests-first where specified). Suggested sequence:
  1) Fix ReviewService syntax
  2) Red tests (parser)
  3) Parser implementation
  4) Red tests (SQLite schema migration v2)
  5) Index schema+migration (to/cc/body_text)
  6) MySQL archive migration
  7) Archive importer CLI
  8) devenv wrappers (extract-attachments, sync-weekly)
  9) Web search + download routes
  10) QA script

## Success Criteria
- Weekly cron can run `devenv run sync-weekly` and updates local Maildir + attachments + SQLite indexes.
- Search UI returns results across mailboxes using MySQL FULLTEXT.
- Users can download raw email and attachments from search results via authenticated, path-guarded endpoints.
- No deletion/decision workflows are added or modified.

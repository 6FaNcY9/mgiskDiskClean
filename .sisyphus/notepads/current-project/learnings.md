# Current Project Learnings

## Task 1 — decisions.csv schema extension (2026-03-26)

- `decisions_template.py` uses a `_HEADERS` list as single source of truth for column order; `DictWriter(extrasaction="raise")` enforces no extra keys.
- `generate_decisions_template()` extracts attachment info by iterating `rec["parts"]` — the parser already excludes body-only (text/plain/html without filename) parts, so all items in `parts` are real attachment/inline parts.
- `is_duplicate` is derived solely from `dup_group_id is not None` (no separate flag needed); `dup_rank=0` is a valid duplicate (canonical member), so it serialises as "0" not "".
- `serialize_decisions_csv` uses `csv.DictWriter` with `lineterminator="\r\n"` — CSV standard; `extrasaction="raise"` catches schema drift early.
- Only 3 files changed: `decisions_template.py`, `test_decisions_template.py`, `test_e2e_cli.py`.
- 61 unit tests + 32 e2e tests pass; no LSP errors.

## Task 1 — subject truncation + evidence files (2026-03-26, retry)

- Subject truncation follows PDF policy exactly: `subject[:80] + "…"` if `len(subject) > 80` (using `_SUBJECT_MAX = 80`; exactly 80 chars passes through unchanged).
- `_truncate_subject()` is a private helper in `decisions_template.py` that mirrors `pdf.py` lines 294 and 397.
- Evidence files created at `.sisyphus/evidence/current-project/task-1-decisions-schema.txt` and `task-1-decisions-schema-dup.txt`.
- Final count: 65 unit tests (decisions_template) + 32 e2e tests = 97 total, all passing.

- Evidence consistency pitfall: avoid leaving stack traces or assertion tracebacks inside an evidence file while also marking the scenario as `PASS`. If a run produced a traceback, re-run the deterministic check and replace the traceback block with the actual deterministic outputs and a clear PASS/FAIL line so the evidence matches reality.

## Task 2 — PHP skeleton + devenv wiring (2026-03-26)

- `languages.php.package = pkgs.php83` in devenv.nix pins PHP 8.3 (currently 8.3.30); `languages.php.enable = true` makes it available in the shell.
- `services.mysql` in devenv.nix uses MariaDB by default; `initialDatabases` + `ensureUsers` auto-creates the dev DB and socket-auth user on first start.
- devenv scripts use `exit 0` + `--help` pattern; all scripts must emit usage and exit 0 for the acceptance `--help` check to pass.
- `store-mailbox` uses `--src` override to allow local rsync testing (rsync works with local paths); essential for QA without a live server.
- PHP `set_exception_handler` + `set_error_handler` together suppress all stack traces from HTTP responses; only a generic message is emitted with http_response_code(500).
- Missing `local.php` safe failure: `is_file($configPath)` check before `require`; returns HTTP 500 with a non-sensitive message. Log the real path server-side.
- PHP built-in server (`php -S host:port -t docroot`) must be started inside `devenv shell` because the `php` binary is not on PATH outside it.
- Pre-store dedup hook in `store-mailbox` checks for module availability first; graceful skip avoids blocking the workflow until Task 2a is implemented.
- Scope guardrail encoded: the only path passed to the dedup module is `$DATA_ROOT` (= `$DEVENV_ROOT/data/mailboxes/<mailbox>/`); rsync source is never passed to dedup.
- Pipeline output naming: `maildir_report` names outputs from the Maildir basename, so `rsync -> maildir/.maildir/` produces `maildir.pdf`, `maildir.manifest.json`, `maildir.decisions.csv` under `reports/`.

## Task 2a — Pre-store dedup (2026-03-26)

- `pre_store_dedup.py` operates at raw file-bytes level (SHA-256 of full .eml file), NOT at attachment-content level; this is intentionally different from Task 2b's attachment hash index.
- Canonical selection uses `(mtime_utc_str, filepath)` sort key — mtime is available without a full parse, filepath ensures unique tiebreaker.
- `candidate_set_hash` = SHA-256 of newline-joined sorted absolute filepaths in the set; this is stable and collision-resistant without needing parsed email metadata.
- Quarantine layout: `<quarantine_root>/<csh[:8]>/<filename>` keeps related quarantined files grouped by candidate set, navigable by humans.
- `audit.log` is **appended** on each run (never overwritten); multiple store-mailbox runs accumulate a full history.
- `store-mailbox` hook in devenv.nix was already wired with a graceful skip; Task 2a makes the module real so the skip no longer triggers.
- `run_pre_store_dedup()` returns `DedupResult` with `candidate_sets`, `quarantined_paths`, `audit_log_path`, `dry_run` — sufficient for downstream automation or reporting.

## Task 2b — Attachment extraction + index (2026-03-26)

- `extract_attachments.py` uses `sha256_hex` from `hash.py` (existing module); no new hash utilities needed.
- Stored filename format: `<sha256>_<size>.<ext>` — collision-safe and idempotent; the original MIME filename is NEVER used as the on-disk path component.
- `_safe_extension()` uses `pathlib.Path(stem).suffix` after stripping to the final path component; `.gitignore` and no-extension files return `"bin"` (correct behavior).
- Zero-byte parts (`size == 0`) are excluded from extraction; they carry no useful content.
- Synthetic inline part labels like `[inline plain]` are also excluded by the `_is_extractable_part()` function.
- Path traversal from MIME headers is neutralised: only `sha256_size.ext` is used, never the original filename as a path.
- `index_mailbox.py` uses `INSERT OR REPLACE` (upsert) for idempotence; `attachments` PRIMARY KEY is `(stored_path, email_stable_id)` to allow multiple emails to link to the same stored file.
- `from_addr` column name used instead of `from` because `from` is a SQL reserved word.
- `conn.row_factory = sqlite3.Row` must be set before executing queries in tests if you need column-name access; tests that open their own connection must set it explicitly.
- devenv.nix `index-mailbox` and `index-all` scripts were pre-wired in Task 2 skeleton; no devenv.nix changes needed for Task 2b.
- 47 new tests added; 479 total tests pass; 0 regressions.

## Task 3 — MySQL schema and migration runner (2026-03-26)

- `is_file()` returns false for Unix socket files in PHP; use `file_exists()` instead to detect the socket before building the PDO DSN. This is the critical fix in `migrate.php` line 58.
- `file_exists()` correctly identifies socket files (type `s` in filesystem) and returns true; `is_file()` only returns true for regular files.
- `--skip-grant-tables` on MariaDB allows any user to connect without authentication; useful for local dev bootstrapping when `devenv up` is not running.
- `schema_migrations` table acts as the schema version tracker; each row is a migration filename + applied_at timestamp. Idempotency achieved by comparing against this set before applying.
- `001_initial_schema.sql` uses `CREATE TABLE IF NOT EXISTS` for all tables; the migration runner also handles idempotency at the file-name level, so double protection.
- Connection failure output: PDO's `SQLSTATE[HY000] [2002] Connection refused` is safe — it reveals no credentials. The password is passed as a PDO constructor argument and never appears in exception messages.
- `decisions.decision` column uses `ENUM('keep','delete','unsure','')` NOT NULL DEFAULT '' — allows empty string as "unset" state without NULLs.
- `emails.dup_rank` defaults to `-1` (not NULL) to avoid nullable integer handling in PHP; `-1` means "not a duplicate".
- `updated_at` in `decisions` uses `ON UPDATE CURRENT_TIMESTAMP` — auto-updates on every decision change without application code.
- Foreign key constraints (`fk_emails_report`, `fk_decisions_report`) reference `reports(report_id)` to enforce referential integrity; importer must insert into `reports` first.
- InnoDB confirmed on all tables; prefix indexes on `subject(64)` and `sender(128)` provide substring search support at reasonable index size.

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

## Task 4 — Report import flow (2026-03-26)

- `report_id = manifest["pdf_sha256"]`; the manifest always has this field as a hex string or null. Null is rejected with HTTP 400.
- Manifest file naming follows Maildir basename convention (not mailbox folder name): store-mailbox always produces `maildir.manifest.json` (basename = `maildir`). The importer accepts an optional `report_name` param defaulting to `'maildir'`.
- `assertPathUnderDataDir()` uses `realpath()` on the **parent directory** (not the file itself) so it works on non-existent files. Then re-appends `basename()` to build the canonical path. `str_starts_with($resolved, $allowedRoot . DIRECTORY_SEPARATOR)` is the guard.
- Duplicate code lines in `Importer.php` from overlapping edits cause silent fallback to manifest-only mode (empty display fields). Fixed by ensuring only one assignment to `$decisionsPath`.
- Per-email display fields (folder, date, sender, subject, size) come from `decisions.csv`, not the manifest. Manifest provides `email_stable_ids` + `dup_groups` only. Both sources are merged in `parseEmailsFromCsv()`.
- `INSERT ... ON DUPLICATE KEY UPDATE` gives upsert semantics for both `reports` and `emails`; re-import of the same `report_id` is fully idempotent.
- Schema version check runs before any DB writes; the `pdo->beginTransaction()` block is only entered after all validation passes, so unknown-schema aborts cleanly with no partial state.
- `file_exists()` (not `is_file()`) must be used for the MySQL socket path check, per Task 3 learnings. The PDO factory in `index.php` uses `file_exists($socket)`.

## Task 5 — Auth + Sessions + CSRF (2026-03-26)

- `SessionManager::start()` must be called before any output. It calls `session_set_cookie_params()` before `session_start()` — parameter order matters.
- CSRF token must be extracted from the **same** curl invocation that saves the cookie jar (`-c $JAR`). A second GET creates a new session with a different token; POSTing the first token against the second session causes 403. QA scripts must use one GET to capture both cookies and HTML.
- `session_regenerate_id(true)` in `SessionManager::login()` invalidates the old session ID — the `true` arg deletes the old session file, preventing session fixation. Subsequent CSRF token generation (via `CsrfGuard::getToken()`) uses the new session.
- `hash_equals()` for CSRF comparison is timing-attack-safe and mandatory; never use `===` for token comparison.
- `SameSite=Strict` on the session cookie provides defense-in-depth against CSRF cross-origin requests, complementing the synchronizer token.
- PHP `session_set_cookie_params(['secure' => false])` required for plain HTTP local dev; the `SessionManager` auto-detects HTTPS via `$_SERVER['HTTPS']` or `HTTP_X_FORWARDED_PROTO`.
- `requireRole()` calls `requireAuth()` internally — always safe to call either; role check implies auth check.
- `POST /review/update` enforces `updated_by` from session display_name (mandatory for coworker), making it impossible to submit a decision without an identity. This is enforced at both login (display_name required for coworker) and update time.
- CSRF token in JSON API body: `CsrfGuard::enforce()` checks `$_POST['csrf_token']` first, then `$_SERVER['HTTP_X_CSRF_TOKEN']`. For JSON endpoints, the caller must send CSRF as an `X-CSRF-Token` header or as a form field (not in the JSON body).
- PHP built-in server (`php -S`) runs single-threaded; `sleep 0.4` in QA scripts is sufficient for local testing but would need adjustment under load.

## Task 6: UI Implementation
- Simple PHP front-controller routing with `parse_url` is effective for small apps but requires careful ordering of definitions (like `$path` vs `$session`).
- Vanilla JS `fetch` is sufficient for "app-like" behavior (decision updates) without full frontend framework overhead.
- Keeping UI templates inline in `index.php` is getting unwieldy (~800 lines); future refactoring should extract views.

## Task 11 — IMAP ingestion (2026-03-28)

- `ImapIngestConfig` is a `@dataclass` with `__post_init__` enforcing `ssl=True`; raising `ValueError` with message containing "TLS" at construction time satisfies the TLS requirement at the type level.
- `ImapMessage(uid, rfc822)` is a plain dataclass — allows tests to inject fake messages without any live connection or `imap-tools` import.
- `materialize_maildir()` is the pure I/O layer: takes pre-fetched messages + uidvalidity + config, writes files. `run_imap_ingest()` is the orchestration layer that optionally accepts an injected `connection` for DI.
- Optional dependency `imap-tools` wrapped in `try/except ImportError` inside `run_imap_ingest()` — the module imports cleanly and all tests pass without `imap-tools` installed.
- Filename scheme `{uidvalidity}.{uid}.eml` in `Maildir/cur/` is deterministic: same UID on same server always writes to the same path (overwrite = idempotent).
- `pyproject.toml` uses `[project.optional-dependencies] imap = ["imap-tools>=1.6"]` — install with `pip install "maildir-pdf-report[imap]"`.
- `devenv.nix` adds `imap-tools>=1.6` to the venv requirements so it's available in devenv shell; a new `fetch-imap` script wires the CLI.
- Adding `--source {rsync,imap}` to `cli.py` required no changes to `build_pipeline()` — the IMAP path materialises a Maildir first, then the existing pipeline runs on it unchanged.
- `_build_parser()` changes are backward-compatible: existing positional args unchanged, new flags are all optional with safe defaults.
- 23 new tests; 518 total; 0 regressions.

## Task 12 — apply_decisions CLI (2026-03-28)

- `cmd_plan` computes `candidate_set_hash` as SHA-256 of sorted stable_ids joined by newlines; this is deterministic across runs even when plan_id and timestamps differ.
- `cmd_apply` validates ALL candidate filepaths against maildir_root (via `_assert_under_root()`) before moving any file; single traversal violation aborts with exit 1 and no files moved.
- Idempotency: `os.path.exists(dest) and not os.path.exists(src)` detects already-quarantined state; re-apply skips those with a clear note and continues (exit 0).
- `audit.jsonl` is written after all moves complete (not incrementally); append-only semantics are guaranteed because `purge` never deletes it.
- `_parse_iso` / `_iso` use a fixed `%Y-%m-%dT%H:%M:%SZ` format without microseconds — avoids Python 3.10 fromisoformat incompatibility with trailing Z.
- Purge retention check uses `entries[0]["moved_at"]` (first quarantine event) rather than dir mtime — more portable and independent of filesystem clock drift.
- Pyright `reportMissingImports` in test files is a pre-existing codebase-wide issue (no `src/` on Pyright's path); all 518 tests pass with `PYTHONPATH=src`.
- 16 new tests added; 518 total (0 regressions).

## Task 12 compliance fixes (2026-03-28)

- `plan --dry-run` must WRITE the plan JSON (preview-then-execute workflow). Plan.764 says "writes a plan file"; original code skipped writing on dry-run — wrong. Fix: move plan-building before the dry-run branch; print different message on dry-run.
- `cmd_apply` reads `mode = plan.get("mode", "quarantine")` and branches on it: `delete` → `os.remove(src)` with audit under `.cleanup_log/<plan_id>/audit.jsonl`; `quarantine` → `shutil.move(src, dest)` with audit under `.quarantine/<plan_id>/audit.jsonl`.
- `audit.jsonl` opened with `"a"` (append), never `"w"`. Re-apply accumulates entries; 4 total after 2 runs on same plan.
- For `mode=delete`, audit goes under `.cleanup_log/<plan_id>/` to avoid creating a `.quarantine/<plan_id>/` dir (which semantically implies files are there to restore).
- `dest = _quarantine_path_for(...)` must be computed unconditionally at the top of the per-candidate loop (before the `if dry_run` check) since dry-run for quarantine mode prints the destination path.
- Missing `return 0` at end of `cmd_apply` caused `None` return (treated as error by tests). Always close function with explicit `return 0`.
- After fix: 20 task-12 tests pass; 522 total (0 regressions).

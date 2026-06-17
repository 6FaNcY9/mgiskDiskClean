# Current Project Issues

## Task 1 — No blockers (2026-03-26)

- Prior attempt had scope creep; this run was clean with strict 3-file scope.
- `test_e2e_cli.py:test_e2e_decisions_has_header_row` hardcodes the full 14-column header list — downstream test authors must keep this in sync with `_HEADERS` in `decisions_template.py`.

## Task 1 retry — No blockers (2026-03-26)

- Prior run was missing: subject truncation matching PDF policy, and the two required evidence files.
- Both gaps resolved: `_truncate_subject()` added, evidence files at `.sisyphus/evidence/current-project/`.
- All 97 tests pass (65 unit + 32 e2e).

## Task 2 — Issues and gotchas (2026-03-26)

- **PHP not on PATH outside devenv shell**: All QA commands that invoke `php` directly must run inside `devenv shell`. Bare `php` outside the shell produces "command not found".
- **devenv shell startup time**: `devenv shell -- <cmd>` adds ~10-15s startup overhead (task resolution). Use a single bash invocation with multiple commands to amortize this.
- **Pipeline output naming caveat**: `store-mailbox` rsyncs to `maildir/.maildir/`, so pipeline names outputs `maildir.*`. If the mailbox name is needed in output files, the caller would need to rename or use a wrapper. Acceptable for v1 per plan.
- **services.mysql requires `devenv up`**: The MariaDB service is a process managed by devenv; it doesn't start automatically with `devenv shell`. `db-start` invokes `devenv up` to start it.

## Task 2a — No blockers (2026-03-26)

- Using mtime as sort key instead of parsed email Date header is a deliberate trade-off: avoids a full parse inside the dedup scan, at the cost of using file mtime which can change after rsync (rsync `-a` flag preserves mtime, so this is safe in the store-mailbox workflow).
- `_candidate_set_hash` expects pre-sorted input; callers must sort before passing. This invariant is enforced within `_build_candidate_sets` and tested directly.
- All 432 tests pass after adding Task 2a (29 new tests); no regressions.

## Task 2b — No blockers (2026-03-26)

- Initial design used `stored_path` as attachments PRIMARY KEY; this caused duplicate-content emails to share one attachment row and lose one linkage. Fixed by using `(stored_path, email_stable_id)` composite PK — each email-attachment relationship is a distinct row.
- `dict(sqlite3.Row)` fails with "dictionary update sequence element has length N; 2 is required" — must use `{k: row[k] for k in row.keys()}` or simply access `row["column"]` directly after setting `conn.row_factory = sqlite3.Row`.
- `[inline <subtype>]` synthetic filenames assigned by parser.py are correctly excluded from extraction via the `filename.startswith("[inline ")` guard.

## Task 3 — Migration runner fix (2026-03-26)

- **`is_file()` vs `file_exists()` for Unix sockets**: The Task 2 skeleton used `is_file($socket)` to check if a socket path exists before building the PDO DSN. `is_file()` returns false for Unix socket files, causing the runner to always fall back to TCP (`host:port`) connection even when `--socket` was specified. Fixed by replacing with `file_exists($socket)`.
- Only one file changed: `web/src/cli/migrate.php` line 58.
- All other infrastructure (migrations SQL, config, devenv scripts) was already correct and complete from Task 2.

## Task 4 — Import issues (2026-03-26)

- **Duplicate code lines from sequential edits**: Two overlapping edit operations left both the old `$decisionsPath = $reportsDir . '/' . $mailbox . ...` and new `$reportName` line in `Importer.php`. PHP uses the last assignment, so old `$mailbox`-based path was active. Result: `is_file()` returned false (file named `testbox.decisions.csv` doesn't exist), silently falling back to manifest-only rows with empty display fields. Fix: replace the full range with a single correct assignment.
- **PHP built-in server inherits env vars**: `DEVENV_ROOT` IS inherited when server is started from devenv shell; `getenv('DEVENV_ROOT')` works correctly in HTTP context.
- **`realpath()` fails on non-existent files**: Must use parent-dir + basename pattern for path guard on files that may not exist yet.

## Task 4 scope fix (2026-03-26)

- Scope creep: `import-report` devenv script + welcome-message entry added to `devenv.nix` during Task 4 were out-of-scope (Task 4 scope = `web/src/**`, `web/public/index.php`, evidence only). Both removed; `devenv.nix` restored to Task-2 state.

## Task 5 — Auth issues and gotchas (2026-03-26)

- **QA script curl cookie/CSRF sync bug**: Original QA script called `curl -c $JAR` twice — once to get HTML and once for status code. Each GET creates a new session with a fresh CSRF token. POSTing the first run's token against the second session fails 403. Fix: capture HTML body and cookies in one `curl -s -c $JAR` call, then reuse that body and cookie jar for the POST.
- **`--data-urlencode` vs `-d` for form POSTs with special chars**: `-d` does not URL-encode values; `--data-urlencode "key=value"` handles spaces/special chars correctly. Critical for CSRF tokens that may contain `-` or `_` (base64url chars). Switched all QA POST args to `--data-urlencode`.
- **`session_set_cookie_params()` must be called BEFORE `session_start()`**: Setting cookie params after start has no effect on the current session. `SessionManager::start()` sets params first, then calls `session_start()`, then enforces idle timeout.
- **CSRF JSON API endpoint**: `POST /admin/import` uses JSON body (not form), so `$_POST['csrf_token']` is empty. Must use `X-CSRF-Token` header or add `csrf_token` as a query param / form field. Task 5 leaves this documented but enforcement uses `$_POST['csrf_token']` only for now; JSON callers use form encoding or X-CSRF-Token header.
- Fix: Task 6 UI CSRF failure. The backend `CsrfGuard::enforce()` expects `$_POST['csrf_token']` or `X-CSRF-Token` header, but frontend was sending it in JSON body. Fixed by updating `fetch` call to send `X-CSRF-Token` header.
- Fix: Task 6 verification failed due to `htmlspecialchars(null)` TypeError crashing the review page (PHP 8.1+), which prevented CSRF token rendering and caused truncated output. Fixed by defaulting nullable fields (note, sender, subject) to empty strings.

## Task 11 — IMAP ingestion issues (2026-03-28)

- **`imap-tools` not installed in current venv**: LSP reports `reportMissingImports` for `imap_tools`. This is expected — `imap-tools` is an optional dependency. The import is inside `try/except ImportError` in `run_imap_ingest()`. Tests pass without it because they inject a mock connection. Devenv needs to be re-entered after `devenv.nix` change for the package to be installed.
- **`pyproject.toml` duplication bug**: An edit accidentally created a duplicate `[project.optional-dependencies]` section. Fixed by deleting the second occurrence. Lesson: when inserting into an existing section, check the full section isn't already being split by the edit.
- **No live IMAP in CI**: All 23 tests use mock connections or test only config/materialization logic. This is intentional — live IMAP tests would require credentials and a test server. The `imap-tools` integration path is tested via the acceptance criteria at the CLI level when real credentials are available.

## Task 11 test fix — 2026-03-28

- `test_main_source_rsync_default_unchanged` called `_build_parser().parse_args()` without `--timestamp`, which is now `required=True`. Fixed by adding `["--timestamp", "2024-01-01T00:00:00", ...]` to the arg list.
- All 518 tests pass after the fix.
- Evidence: `.sisyphus/evidence/current-project/task-11-imap-fix.txt`

## Task 12 compliance gaps found and fixed (2026-03-28)

1. `plan --dry-run` did not write plan file — violated plan.764 "writes a plan file". Fixed.
2. `cmd_apply` ignored `plan["mode"]` — always quarantined even when mode=delete. Fixed.
3. `audit.jsonl` opened with `"w"` (overwrite) — second apply destroyed first audit. Fixed to `"a"` (append).
4. `cmd_apply` was missing `return 0` at end of function — returned `None`, tests saw `None == 0` failures. Fixed.
5. `dest` variable used in dry-run print before it was assigned — `NameError` risk. Fixed by hoisting `dest` computation to top of loop body.

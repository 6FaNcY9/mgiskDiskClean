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

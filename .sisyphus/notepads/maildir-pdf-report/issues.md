## Task 1 QA Failure — Scope Creep (2026-03-24)

**Root cause**: The working tree was already dirty at task start — it contained pre-existing uncommitted changes to `.gitignore`, `README.md`, `nginx/nginx.conf`, `scripts/disk_scan.py`, `scripts/maildir_scan.py`, `scripts/maildir_viewer.py` (unrelated feature work not yet committed). These were mistakenly read as part of the session's state and were not reverted before delivery, causing the QA scope gate to fail.

**Fix applied**: Ran `git checkout HEAD -- <6 files>` to restore all unrelated tracked files to HEAD. Rewrote `devenv.nix` from the HEAD baseline, adding only the `languages.python` venv block.

**Prevention**: Always run `git status --short` at task start and explicitly confirm which files the task is permitted to touch. Any pre-existing dirty tracked files outside task scope must be noted and left alone (not carried forward in delivery).

## Task 3 QA Fix — Pyright annotation error (2026-03-24)

**Error**: `Type "_EncodedPayloadType | Any" is not assignable to declared type "bytes | None"` at `parser.py:221`.
**Root cause**: Inline annotation `payload: bytes | None = part.get_payload(decode=True)` was narrower than pyright's stdlib stub for `get_payload(decode=True)`.
**Fix**: Dropped the inline annotation; runtime `isinstance` guard on line 225 already ensures safe narrowing before use.
**Status**: Resolved. No diagnostics. 58 tests pass.

## Task 4 — No issues (2026-03-24)

`inventory.py` has zero pyright errors. The `reportMissingImports` errors in test files are pre-existing and identical to Tasks 2/3 — pyright uses the system Python rather than the devenv venv. Not a regression.

## Task 5 — No blocking issues (2026-03-24)

- Parser integration tests initially failed because `email.message.Message.attach()` + `.as_bytes()` on a multipart message raises `AttributeError: 'list' object has no attribute 'encode'` in Python 3.13. Fixed by using `MIMEMultipart`/`MIMEApplication` (same pattern as `test_strict_parse.py`).
- `reportMissingImports` on test files for `pytest` and `maildir_report.*` — same pre-existing Pyright issue as Tasks 2–4 (system Python vs devenv venv). Not a regression.

## Task 6 — No blocking issues (2026-03-24)

- LSP diagnostics on `dedup.py`: zero errors.
- `reportMissingImports` on `test_dedup_group.py` for `pytest` and `maildir_report.*` — same pre-existing Pyright noise as Tasks 2–5 (system Python vs devenv venv). Not a regression.
- `random.shuffle` used in stability tests; `random.seed(seed)` is called before each shuffle, making the test inputs deterministic despite using `random`. The function under test (`group_emails`) itself contains zero random/time inputs.

## Task 7 — No blocking issues (2026-03-24)

- `reportAttributeAccessIssue` from pyright on `sub_msg.as_bytes()` when `sub_msg` was not narrowed: fixed by `isinstance(sub_msg, _emsg.Message)` guard with a top-level `import email.message as _emsg`.
- `import email.message as _emsg` must be at the module top level, not inline inside the loop body (inline import triggers its own pyright directive-comment mis-parse warning).
- `reportMissingImports` on `tests/test_rfc822.py` for `pytest` and `maildir_report.*` — same pre-existing Pyright noise as Tasks 2–6 (system Python vs devenv venv). Not a regression.

## Task 8 — No blocking issues (2026-03-24)

- `reportMissingImports` on `tests/test_walk_deterministic.py` for `pytest` and `maildir_report.*` — same pre-existing Pyright noise as Tasks 2–7 (system Python vs devenv venv). Not a regression.
- Zero pyright errors on `src/maildir_report/walk.py` and `src/maildir_report/parser.py`.
- The one integration test (`test_parser_scan_folder_labels_match_walk`) was correctly RED before wiring parser.py — `.Drafts` was returned as-is; after wiring it became `Drafts` (correct).

## Task 9 — No blocking issues (2026-03-24)

- `reportMissingImports` on `tests/test_pdf_determinism.py` for `pytest`, `maildir_report.runtime`, and `maildir_report.pdf_determinism` — same pre-existing Pyright noise as Tasks 2–8 (system Python vs devenv venv). Not a regression.
- Zero pyright errors on `src/maildir_report/runtime.py` and `src/maildir_report/pdf_determinism.py`.
- `datetime.fromisoformat("2024-03-20")` parses successfully (returns midnight naive datetime) but the `"T" not in ts_str` guard correctly rejects it with `ValueError`. This was tested in `test_parse_date_only_string_raises_value_error` and confirmed working.
- ast-grep confirms zero `datetime.now()` calls in all changed files.

## Task 10 — No blocking issues (2026-03-24)

- `reportMissingImports` on `tests/test_pdf_german_headers.py` for `pytest`, `maildir_report`, and `maildir_report.pdf` — same pre-existing Pyright noise as Tasks 2–9 (system Python vs devenv venv). Not a regression.
- Zero pyright errors on `src/maildir_report/pdf.py`.
- ast-grep confirms zero `datetime.now()` calls in both changed files.

## Task 15 — No blocking issues (2026-03-25)

- `devenv.nix` was already in the correct state (no serve/tunnel commands, `scan-mailbox` wired to `python -m maildir_report`). Task was verification + evidence capture only.
- The plan referenced `devenv.nix:18-189` as a stale reference from before Task 1 restructured the file — current file is 61 lines. This is documentation drift, not a code problem.

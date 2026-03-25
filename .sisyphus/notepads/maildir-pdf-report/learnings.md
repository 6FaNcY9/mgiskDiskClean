## Task 1 — Python/pytest scaffold (2026-03-24)

- `devenv` `languages.python.venv.enable = true` + `requirements` is the simplest way to get pip packages in this repo; it creates a venv on `devenv shell` init via `devenv:python:virtualenv` task.
- The repo uses `nixpkgs-unstable` so `python3` is already in `packages`; enabling `languages.python` alongside `packages = [...python3...]` is harmless (the language module wraps the same python3 base).
- `devenv shell -- <cmd>` is the correct invocation syntax (not `--command`).
- Verification took ~8s for venv creation on first run, subsequent runs use cached venv.
- Evidence file: `.sisyphus/evidence/task-1-pytest.txt` — shows 3 passed in 0.01s.

## Task 1 QA Fix — Dirty working tree discipline (2026-03-24)

- **Always run `git status --short` at task start** — the working tree may have pre-existing dirty files completely unrelated to the current task.
- `git checkout HEAD -- <files>` is the safe, surgical restore for individual files — it does not disturb untracked files or other staged/unstaged changes.
- When writing `devenv.nix`, always start from `git show HEAD:devenv.nix` as the baseline, then add only the Task-scoped lines. Avoid re-reading the dirty working-tree file as the source of truth.
- The devenv.nix diff for Task 1 is exactly 11 lines added: the `languages.python` block with `venv.enable` and `requirements`. No other lines changed.

## Task 2 — Stable IDs + Deterministic Ordering (2026-03-24)

- `src/` layout with pytest requires adding `pythonpath = ["src"]` to `[tool.pytest.ini_options]` in `pyproject.toml`. Without this, pytest cannot discover the package even though the venv is active.
- `email_stable_id()` uses SHA-256 of `(filepath, message_id)` — `filepath` is the primary key (unique within a scan), `message_id` is a secondary discriminator.  Both joined with a null-byte separator to prevent prefix collisions.
- `part_stable_id()` returns `SHA-256(payload_bytes)` — content-addressable, making duplicate detection trivial: same bytes == same ID.
- `dup_group_stable_id()` sorts member IDs before hashing to be order-invariant (same members in any order → same group ID).
- The anti-pattern `m["id"] = i` was explicitly tested by `test_stable_id_email_not_index_based` — it shuffles records and asserts each record gets the same ID regardless of its position in the list.
- A null-byte (`\x00`) separator between SHA-256 inputs prevents collision between sha256("a"+"bc") and sha256("ab"+"c").
- 20 targeted tests pass; full suite (23) is clean.
- Evidence: `.sisyphus/evidence/task-2-stable-ids.txt`

## Task 3 — Strict Maildir Parser (2026-03-24)

- `MailParseError(filepath, reason)` carries both the path and a human-readable reason; `str(err)` includes both so any caller/test can assert on them directly.
- `parse_email_file()` uses `pathlib.Path.read_bytes()` (raises `OSError` on unreadable files) — wrapping that in a try/except and re-raising as `MailParseError` is the cleanest pattern.
- `email.message_from_bytes()` (Python 3) is preferred over `email.message_from_string()` (legacy Py2 API) — avoids implicit UTF-8 decode issues.
- **No size threshold**: the legacy `if size < 128: continue` and `if size > 512: hash` logic is completely removed. Every part (including zero-byte) gets a `content_hash` and a `stable_id`.
- **_BODY_MIME filter**: body-structure MIME types (`text/plain`, `text/html`, multipart/* variants) without an explicit filename are skipped because they are not attachments — they are the body itself. Named `text/plain` files (e.g. a `.txt` attachment) still pass through.
- `scan_maildir()` sorts `dirs` in-place inside `os.walk` AND sorts `files` per directory — both are necessary for full determinism.
- `scan_maildir()` does NOT catch `MailParseError` — it propagates immediately. This is the "strict mode" contract: one bad file halts the entire scan.
- 35 targeted tests; full suite 58 tests, all green.
- Evidence: `.sisyphus/evidence/task-3-strict-parse.txt`

## Task 3 QA Fix — Pyright payload annotation (2026-03-24)

- `email.Message.get_payload(decode=True)` returns `_EncodedPayloadType | Any` per pyright's stdlib stubs — this is wider than `bytes | None`.
- The runtime `isinstance(payload, (bytes, bytearray))` guard on the next line already handles this correctly; the only problem was the narrow inline annotation `payload: bytes | None` on the assignment.
- Fix: remove the inline type annotation entirely so pyright infers the actual return type. The runtime guard on line 225 (`payload if isinstance(...) else b""`) still enforces `bytes` semantics downstream.
- Rule: never annotate a stdlib call result narrower than the stub declares unless you immediately assert/cast it.

## Task 4 — Audited Inventory Reconciliation (2026-03-24)

- `InventoryMismatchError(missing, extra)` follows the same convention as `MailParseError`: both lists are sorted in `__init__` so error output is byte-for-byte reproducible regardless of set iteration order.
- **`missing`** = paths in parsed records but NOT on disk (phantom/stale entries); **`extra`** = paths on disk but NOT in records (unscanned files). This naming is from the record's perspective: `missing` means "the record expects a file that is missing from disk".
- `list_maildir_files()` mirrors `scan_maildir()` scope exactly: only `cur/` and `new/`, hidden files skipped, `tmp/` excluded. This ensures `reconcile_inventory()` uses the same file universe as the parser — no phantom mismatches from scope differences.
- `os.path.abspath()` is applied to both sides before set comparison to prevent false mismatches when a record uses a relative path vs. list_maildir_files returning absolute paths.
- Test guard: a test assertion using `"tmp" not in path` failed because pytest's own `tmp_path` fixture lives under `/tmp/...` — the string "tmp" matched the system temp dir. Fix: compare against the specific `Maildir/tmp/` prefix with `.startswith()` instead.
- 33 targeted tests; full suite 91 tests, all green.
- Evidence: `.sisyphus/evidence/task-4-inventory.txt`

## Task 5 — SHA-256 collision-resistant hashing (2026-03-24)

- `src/maildir_report/hash.py` exposes a single public `sha256_hex(payload)` — accepts `bytes | bytearray | None`, raises `TypeError` on wrong type, deterministic.
- `parser.py` already used `hashlib.sha256` inline; it now imports `sha256_hex` from `hash.py` — parser.py retains `import hashlib` because `ids.py` remains a separate module with its own internal `_sha256_hex`.
- Python 3.13 `email.message.Message.as_bytes()` on a manually-constructed multipart message (via `.attach()`) fails with `AttributeError: 'list' object has no attribute 'encode'`. Fix: always use `MIMEMultipart` + `MIMEApplication` (same pattern as `test_strict_parse.py`) for test message construction.
- SHA-256 of empty bytes: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (standard constant, tested explicitly).
- ast-grep pattern `hashlib.md5($$$)` with lang `python` confirms zero MD5 in `src/maildir_report/` and `tests/`.
- 16 new tests; full suite 106 tests, all green.
- Evidence: `.sisyphus/evidence/task-5-sha256.txt`

## Task 6 — Duplicate Grouping Semantics (2026-03-24)

- `dedup.py` exposes a single public `group_emails(records)` returning `(annotated_records, dup_groups)`. Both output lists are new objects; the caller's input list and dicts are not mutated.
- Union-Find uses path-halving (parent[x] = parent[parent[x]]) — not path compression by recursion — avoiding stack overflow on large inputs and keeping the algorithm correct on small inputs too.
- The `in_any_cross` set is built by collecting all email indices that appear in ANY cross-hash bucket. This ensures only emails that actually share a hash with another are in groups.
- `member_email_ids` in each `DupGroupRecord` is in `sort_emails()` order (date, filepath) — not stable_id lexicographic order. This matches how the PDF will display members and makes the canonical member (rank 0) the oldest.
- `is_dup` on parts is set to `True` iff the part's `content_hash` is in `cross_hashes` — i.e. the hash appears in ≥ 2 distinct emails. A hash shared by two parts in the SAME email is NOT counted as cross-mail.
- The dedup module imports only from `ids.py` and `ordering.py` — no dependency on `parser.py` or `inventory.py`. This keeps the module testable with pure synthetic records.
- 20 new tests; full suite 126 tests, all green.
- Evidence: `.sisyphus/evidence/task-6-dedup-groups.txt`

## Task 7 — Nested message/rfc822 handling (2026-03-24)

- `part.get_payload()` on a `message/rfc822` part returns `list[Message] | str` per Python's email module. In practice it is always a 1-element list, but pyright types the list items as `str | Message`. Guard with `isinstance(sub_msg, email.message.Message)` to narrow correctly.
- `sub_msg.as_bytes()` serialises the nested RFC 2822 message to raw bytes — this is the canonical way to get a stable byte representation for hashing. It produces the same bytes across parses if the file is unchanged.
- `has_nested_messages: bool` added to EmailRecord. Defaults `False`; set `True` iff any `message/rfc822` part is encountered during walk.
- The `_BODY_MIME` filter runs BEFORE the `message/rfc822` check, so order matters: `message/rfc822` is not in `_BODY_MIME` and would never be caught by that filter anyway — but keeping the checks in the right order makes the intent clear.
- 11 new tests; full suite 137 tests, all green.
- Evidence: `.sisyphus/evidence/task-7-rfc822.txt`

## Task 8 — Deterministic Filesystem Traversal + Folder Naming (2026-03-24)

- `walk.py` exposes two public functions: `normalize_folder_name(raw)` and `deterministic_walk(root)`. Both are pure functions with no side effects.
- `normalize_folder_name` handles all Maildir++ cases: `""` → `"INBOX"`, `"."` → `"INBOX"`, `"cur"/"new"` → `"INBOX"`, `".Sent"` → `"Sent"`, `".INBOX.Work"` → `"INBOX/Work"`. The rule: strip leading dot, then split remaining text on `.`, join with `/`.
- `deterministic_walk` uses `dirs[:] = sorted(d for d in dirs if d != "tmp")` (same pattern as the old inline code) so `os.walk` descends in stable lexicographic order. Files are also sorted per directory before yielding.
- Folder name determination: check if `os.path.abspath(parent) == abs_root` — if so, it's a root INBOX folder; otherwise take `os.path.basename(parent)` and pass through `normalize_folder_name`.
- `parser.py` `scan_maildir` was refactored to one line: `for fpath, folder in deterministic_walk(root)`. The old `os.walk` block (8 lines with inline sorting and manual folder detection) was replaced entirely.
- `import os` was removed from `parser.py` after the refactor — it was only used by the old `scan_maildir` block.
- 35 new tests; full suite 172 tests, all green.
- Evidence: `.sisyphus/evidence/task-8-walk.txt`

## Task 9 — Deterministic Timestamp + PDF Metadata Strategy (2026-03-24)

- `runtime.py` exposes `parse_report_timestamp(ts_str) -> datetime` — converts any ISO 8601 string (with or without offset, with `Z` suffix, or naive) to a UTC-aware `datetime`. Naive strings are assumed UTC, not local time (determinism: no dependence on system timezone).
- `datetime.fromisoformat()` does NOT handle the `Z` suffix in Python < 3.11; normalise `Z` → `+00:00` before calling to be safe (we target Python 3.11+ but the guard costs nothing).
- Date-only strings (e.g. `"2024-03-20"`) are rejected with `ValueError` — they parse successfully through `fromisoformat` but `"T" not in ts_str` detects the lack of a time component. This prevents silent acceptance of incomplete inputs that would produce midnight-UTC timestamps.
- `pdf_determinism.py` exposes `configure_deterministic_pdf() -> None` — single line: `rl_config.invariant = True`. Must be called before any `canvas.Canvas` or `SimpleDocTemplate` is instantiated. Process-global effect; idempotent.
- `rl_config.invariant = True` eliminates CreationDate/ModDate timestamps, the random `/ID` array, and any other entropy injected by ReportLab. With identical input content and identical metadata, SHA-256 of two PDF generations is provably equal.
- TDD verification: `test_many_runs_all_same_sha256` generates 10 PDFs in a set comprehension — if any differ, the set would have >1 element. Clean, idiomatic proof of invariance.
- ast-grep pattern `datetime.now()` confirms zero such calls in `src/maildir_report/` and `tests/`.
- 20 new tests; full suite 192 tests, all green.
- Evidence: `.sisyphus/evidence/task-9-pdf-determinism.txt`

## Task 10 — German PDF Layout (2026-03-24)

- `build_report_pdf(records, dup_groups, timestamp_str) -> bytes` is the single public API. It calls `configure_deterministic_pdf()` unconditionally as the first action before any ReportLab object is created.
- **Font strategy**: Helvetica (built-in Type1 with WinAnsiEncoding) — no external TTF files required. This covers all German special characters (ä U+00E4, ö U+00F6, ü U+00FC, Ä U+00C4, Ö U+00D6, Ü U+00DC, ß U+00DF) without any font registration step.
- **Text extraction for tests**: ReportLab's default content stream encoding is ASCII85Decode + FlateDecode. Decoding via `base64.a85decode(raw, adobe=True)` then `zlib.decompress()` yields raw PDF content stream bytes. Text operands appear as `(text) Tj` — ASCII bytes for ASCII chars, octal escapes (`\344` = 0xE4 = ä) for Latin-1 chars. This allows reliable grep-style assertions without external PDF libraries (pdfminer not available).
- **Truncation of long strings**: subjects > 50 chars and senders > 30 chars are truncated with `…` (U+2026) to prevent table overflow. Only the date-day portion (`"YYYY-MM-DD"`) is shown in the table for brevity.
- **Column widths**: proportional fractions of usable_width (page width minus margins) — `[0.36, 0.22, 0.17, 0.12, 0.13]`. Stable and reproducible.
- `sort_emails()` is used to order records before building the table — same convention as dedup.py and the rest of the pipeline.
- 26 new tests; full suite 218 tests, all green.
- Evidence: `.sisyphus/evidence/task-10-german.txt`

## Task 11 — Compact duplicate-groups PDF section (2026-03-24)
(Note: evidence file task-11-dups.txt was produced in the prior task wave.)

## Task 12 — Audited manifest JSON (2026-03-24)

- `manifest.py` exposes two public symbols: `build_manifest(records, dup_groups, timestamp_str, pdf_bytes=None) -> dict` and `validate_manifest_invariants(manifest) -> None`. `ManifestInvariantError` is the typed exception.
- **Schema version**: `SCHEMA_VERSION = "1.0"` as a module-level constant. Include it in every manifest for forward-compatibility.
- **generated_at**: derived from `runtime.parse_report_timestamp()` + `format_report_timestamp()` — guaranteed UTC ISO 8601, no datetime objects escape the module.
- **dup_email_count** counts records with `dup_group_id is not None` (after `dedup.group_emails` annotation). This is the sum of all `member_email_ids` lengths across all groups — union-find guarantees no email appears in two groups.
- **email_stable_ids ordering**: use `sort_emails(records)` before extracting IDs — consistent with PDF ordering and dedup.py conventions.
- **dup_groups ordering**: use `sort_dup_groups(dup_groups)` — consistent with pdf.py.
- **pdf_sha256**: pass raw PDF `bytes` from `pdf.build_report_pdf()` into `sha256_hex()` from `hash.py`. When no PDF generated, the field is `None` (key always present for schema completeness).
- **JSON serialisability**: the manifest dict contains only `str`, `int`, `list`, `dict`, and `None` — no `datetime`, no `bytes`. This is verified by the JSON round-trip tests.
- **validate_manifest_invariants**: checks 6 invariants. `ManifestInvariantError.violations` is a `list[str]` of all violated invariants (not just the first) — auditors see the full picture in one call.
- 66 new tests; full suite 306 tests, all green.
- Evidence: `.sisyphus/evidence/task-12-manifest.txt`

## Task 13 — Decisions Template Generator (2026-03-24)

- `decisions_template.py` exposes three public symbols: `generate_decisions_template`, `serialize_decisions_csv`, `serialize_decisions_json`.
- `generate_decisions_template(records)` calls `sort_emails(records)` first (new list, no mutation) then extracts `stable_id` and `filepath` verbatim from each record dict — never recomputes IDs.
- `decision` field is always an empty string (`""`); value type is `str` not `None` — ensures CSV/JSON round-trip fidelity and makes "no decision yet" unambiguous.
- `serialize_decisions_csv` uses `csv.DictWriter` with `lineterminator="\r\n"` and `extrasaction="raise"` — RFC 4180 compliant, escapes commas/quotes automatically, crashes loudly on schema drift.
- `serialize_decisions_json` uses `json.dumps(rows, ensure_ascii=False, indent=2)` — preserves dict insertion key order (Python 3.7+), human-readable, non-ASCII filepaths survive round-trip intact.
- `_HEADERS = ["stable_id", "filepath", "decision"]` is a module-level constant — single source of truth for column order in both CSV and JSON.
- ast-grep confirms zero `datetime.now()` calls in both source and test files.
- 40 new tests; full suite 346 tests, all green.
- Evidence: `.sisyphus/evidence/task-13-decisions-template.txt`

## Task 14 — CLI entrypoint + e2e tests (2026-03-24)

- `cli.py` exposes two public symbols: `build_pipeline(maildir_path, output_dir, timestamp_str)` (raises on errors — for programmatic/test use) and `main(argv=None) -> int` (catches all exceptions, returns 0/1 — for `sys.exit` wiring).
- `__main__.py` with `sys.exit(main())` is the minimal boilerplate for `python -m maildir_report` invocation. Only 4 lines required.
- Pipeline order: `scan_maildir` → `reconcile_inventory` → `group_emails` → `build_report_pdf` → `build_manifest(pdf_bytes=pdf_bytes)` → `generate_decisions_template` + `serialize_decisions_csv`. Inventory reconciliation before dedup/PDF so strict-mode errors surface before any expensive work.
- Output filenames are module-level constants (`PDF_FILENAME`, `MANIFEST_FILENAME`, `DECISIONS_FILENAME`) — single source of truth used in both `build_pipeline` and tests.
- `subprocess` e2e test for `python -m` invocation must inject `PYTHONPATH=src` into the subprocess env; pytest's `pythonpath = ["src"]` config only affects the in-process test runner, not child processes. Pattern: `env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")`.
- `capsys` fixture is required for tests that call `main(["--help"])` (argparse prints to stdout; `SystemExit(0)` is raised — use `pytest.raises(SystemExit)`).
- 32 e2e tests, full suite 378 tests, all green.
- ast-grep confirms zero `datetime.now()` calls in all Task 14 files.
- Evidence: `.sisyphus/evidence/task-14-e2e.txt`

## Task 15 — devenv.nix workflow cleanup (2026-03-25)

- The working tree already had the correct `devenv.nix` state from prior task waves — the serving/tunnel commands had been stripped and `scan-mailbox` updated to use `python -m maildir_report`. Task 15 required verification and evidence capture rather than new edits.
- Correct devenv invocation is `devenv shell -- bash -lc '<cmd>'` (not `--command`), as noted in Task 1 learnings.
- The `enterShell` banner now prints only `scan-mailbox` and the reports/logs paths — no serving commands listed.
- Evidence: `.sisyphus/evidence/task-15-devenv.txt` — shows `scan-mailbox is .../bin/scan-mailbox` (EXIT 0) and `serve-start: not found` (EXIT 1).

## Task 16 — Strip Serving Stack (2026-03-25)

- `nginx/` and `cloudflared/` were the only serving-stack directories; deleted both.
- `.devenv/shell-*.sh` generated files contain nginx/serve-* references — these are Nix-generated artifacts, not user-editable sources. They do not need cleaning; they will regenerate correctly once `devenv.nix` is updated (task 15 already removed serve/tunnel commands from devenv.nix).
- `README.md` had 9 serving-stack references across setup, workflow, and commands sections; replaced entirely with scan-mailbox-centric content.
- `scripts/maildir_viewer.py` had a stale docstring comment "deploy this to nginx" — updated to "generated HTML report" (minimal, in-scope fix).
- QA pattern: exclude `.devenv/`, `.git/`, `.sisyphus/` when grepping for serving keywords in user files.
- Evidence file: `.sisyphus/evidence/task-16-strip-serving.txt`

## Task 16 correction — README output filenames (2026-03-25)

Initial README (task 16) incorrectly claimed mailbox-prefixed outputs (`<mailbox>.pdf`,
`<mailbox>.manifest.json`, `<mailbox>.decisions.json`).  The actual CLI constants in
`src/maildir_report/cli.py` are fixed names written to `<output_dir>/`:
  PDF_FILENAME       = "report.pdf"
  MANIFEST_FILENAME  = "manifest.json"
  DECISIONS_FILENAME = "decisions.csv"

Lesson: always verify output filenames against module-level constants in cli.py, not
intuition about naming conventions.

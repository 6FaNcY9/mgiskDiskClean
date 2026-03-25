## Task 1 — Scaffold approach (2026-03-24)

**Decision**: Use `languages.python.venv` (pip-based) rather than adding `python3Packages.pytest` / `python3Packages.reportlab` directly to the nix `packages` list.

**Rationale**: Nixpkgs Python packages are hermetic but harder to version-pin flexibly; pip requirements in a venv mirror normal Python dev workflows and make it easy for Task 2+ to add new dependencies with a single `requirements` line. The venv is managed by devenv automatically.

**pyproject.toml style**: Minimal `[build-system]` + `[project]` + `[tool.pytest.ini_options]`. No unnecessary metadata. `testpaths = ["tests"]` makes `pytest -q` unambiguous.

**`python3` kept in packages**: The existing `packages = [... python3 ...]` entry is retained; `languages.python.enable = true` doesn't conflict and ensures the venv wrapper scripts are available.

## Task 2 — ID derivation strategy (2026-03-24)

**Decision**: Use SHA-256 hex strings (64 chars) for all stable IDs.

**Rationale**:
- Content-addressed: same input → same ID, different input → different ID (with overwhelming probability).
- Hex strings are JSON-serialisable, filesystem-safe, and human-inspectable.
- SHA-256 is already planned for attachment hashing (Task 5) — consistent algorithm throughout.

**Decision**: `email_stable_id = SHA-256(filepath + "\x00" + message_id)`.

**Rationale**: `filepath` is the canonical Maildir-unique discriminator (each Maildir file has a unique name per RFC). `message_id` adds a semantic cross-reference. Null-byte separator prevents trivial collision attacks.

**Decision**: Separate module `src/maildir_report/ordering.py` (not inlined into models or ids).

**Rationale**: Ordering rules are policy decisions that may change (e.g., parts ordered by size descending in a future task). Keeping them in a dedicated module makes them easy to find, test, and override without touching data-model or ID logic.

**Decision**: `sort_emails` sorts by `(date, filepath)` — NOT by stable_id.

**Rationale**: Date ordering is human-meaningful in the PDF (emails appear chronologically). Filepath is the tiebreaker because it's unique and stable.  Sorting by stable_id would produce an opaque random-looking order.

**Decision**: `sort_dup_groups` sorts by `min(member_email_ids)` — the canonical (lexicographically smallest) member stable ID.

**Rationale**: Groups don't have natural human ordering; using the smallest member ID gives a reproducible and deterministic order without requiring additional metadata.
## Task 3 — Parser Design Decisions (2026-03-24)

### Decision: MailParseError vs returning sentinel
**Choice**: Raise `MailParseError` always; never return `None` or a sentinel dict.
**Reason**: The plan mandates "fail the run if any file cannot be parsed cleanly". A typed exception with `filepath` + `reason` attributes gives callers (Task 4 reconciliation, CLI) precise context without string-parsing the message. Returning `None` (legacy pattern) creates silent correctness holes.

### Decision: No size threshold for parts
**Choice**: Remove the `if size < 128: continue` and `if size > 512: hash` guards entirely.
**Reason**: The plan states "no silent dropping of mail parts". Small attachments (tiny PDFs, signature files) are legitimate parts. Hashing every part regardless of size is required for correct duplicate detection in Task 5/6.

### Decision: Empty file → MailParseError
**Choice**: A file with `len(raw) == 0` raises immediately before `email.message_from_bytes`.
**Reason**: An empty file cannot be a valid RFC 2822 message. Attempting to parse it would succeed (Python's email parser is permissive) but produce a record with no headers — this is semantically broken and should fail loudly, not produce a phantom record.

### Decision: scan_maildir propagates MailParseError (no try/except)
**Choice**: Errors from `parse_email_file` are NOT caught inside `scan_maildir`.
**Reason**: Task 4 will perform a file-count reconciliation. If the scanner silently skipped bad files, the reconciliation count would appear to match while data was missing. Propagating the error immediately makes the bad-file state visible to the caller/CLI.

### Decision: pathlib.Path.read_bytes() for I/O
**Choice**: Use `pathlib.Path(filepath).read_bytes()` instead of `open(filepath, "rb").read()`.
**Reason**: More idiomatic Python 3; raises `OSError` (not a bare `except`) which we catch specifically and re-raise as `MailParseError`. Avoids unclosed file handles in error paths.

### Decision: Parser API is file-level (parse_email_file), not object-level
**Choice**: Free function `parse_email_file(filepath, folder)` rather than a class.
**Reason**: Task 4 reconciliation needs to call parsing from an inventory loop. A stateless free function is the simplest composable unit. No parser state needed between files.

## Task 5 — SHA-256 hashing module design (2026-03-24)

### Decision: Dedicated `hash.py` module with single public function
**Choice**: `src/maildir_report/hash.py` exposes only `sha256_hex(payload) -> str`.
**Reason**: `ids.py` already has a private `_sha256_hex(*parts)` that concatenates multiple inputs with null-byte separators (for stable ID derivation). Task 5's purpose is single-payload content hashing — a distinct concern. Keeping it in a separate module keeps responsibilities clear and avoids ambiguity between multi-part identity hashing and single-payload content hashing.

### Decision: `None` input maps to empty bytes (not an error)
**Choice**: `sha256_hex(None)` returns SHA-256 of `b""` rather than raising.
**Reason**: Parser's `get_payload(decode=True)` can return `None` for empty/degenerate parts. Treating `None` as empty bytes makes the function safe to call directly on parser output without an extra guard, and the empty-bytes hash is deterministic and meaningful.

### Decision: `parser.py` imports `sha256_hex` from `hash.py`
**Choice**: Replace inline `hashlib.sha256(payload_bytes).hexdigest()` with `sha256_hex(payload_bytes)`.
**Reason**: Makes the canonical hashing path explicit and testable at the module level. If the hash algorithm ever changes, only `hash.py` needs updating — not scattered inline calls.

## Task 6 — Duplicate Grouping Design Decisions (2026-03-24)

### Decision: `group_emails()` is a pure function (no mutations of input)
**Choice**: Return new dicts; never mutate the caller's records in place.
**Reason**: Callers may hold references to the original records (e.g. for reconciliation). Mutating would cause hidden state changes. Returning new lists with shallow copies is safe and composable.

### Decision: `member_email_ids` ordered by `sort_emails()` (date, filepath), not by stable_id
**Choice**: Sort group members via `sort_emails()` to determine both `member_email_ids` order and `dup_rank`.
**Reason**: The canonical member (rank 0) is the oldest email — the same convention as the legacy script (`dup_rank = 0` = oldest). Sorting by stable_id would produce an opaque hex-order with no human meaning. Date ordering matches the PDF display expectation.

### Decision: `is_dup` on parts scoped to *cross-mail* hashes only
**Choice**: A content_hash is only flagged `is_dup=True` if it appears in ≥ 2 *distinct* emails (cross-mail). Two identical parts within the same email do NOT trigger `is_dup`.
**Reason**: Intra-mail duplicate parts are a MIME encoding quirk, not a dedup signal. The dedup report is about detecting the same attachment in multiple different emails.

### Decision: No wiring changes to `parser.py`, `models.py`, or `__init__.py`
**Choice**: `dedup.py` is self-contained; no changes required in existing modules.
**Reason**: The data model already carries `dup_group_id`, `dup_rank`, `is_dup` fields on both `EmailRecord` and `PartRecord`. The `ids.py` and `ordering.py` modules already provide all helpers needed. No new dependencies introduced.

## Task 7 — Nested message representation (2026-03-24)

### Decision: represent nested message/rfc822 as a synthetic part in parts inventory
**Choice**: When a `message/rfc822` part is encountered, create a `PartRecord`-shaped dict with `category="nested_message"`, `filename="[nested message]"`, `mime="message/rfc822"`, and stable hashes of the serialised sub-message bytes. Add `has_nested_messages=True` to the outer `EmailRecord`.
**Reason**: The plan requires "explicit indicator presence" and "no silent ignore". Options considered:
  1. Recurse into sub-message and merge its parts: deferred to Task 8 (traversal normalisation) as explicitly forbidden here.
  2. Add only a flag, no part entry: would satisfy the flag requirement but leave the parts inventory incomplete — downstream PDF/dedup code would never see the nested content.
  3. Add a synthetic part + flag (chosen): surfaces both presence and content hash in the standard inventory structure. Future Task 8 can replace the synthetic part with fully-expanded sub-parts without changing any callers — they will just see more parts.
**Tradeoff**: The `payload_bytes` field on the nested part contains the full RFC 2822 bytes of the sub-message (not decoded content). This is correct for hashing/dedup and will be superseded by Task 8 if deeper access is needed.

## Task 8 — Deterministic Walk Decisions (2026-03-24)

- **Extracted to `walk.py` not inline in `parser.py`**: Keeps the walk/normalization concern separate and independently testable. `parser.py` becomes a pure consumer.
- **`normalize_folder_name` is a pure function (no side effects)**: Enables property-style testing; each case tested independently without filesystem setup.
- **Folder detection uses `abspath(parent) == abs_root` not `relpath`**: Avoids Windows path separator issues (no `replace("\\", "/")` needed). Cleaner and more robust.
- **`deterministic_walk` yields `(filepath, folder_name)` tuples**: Bundles the two pieces of information a caller needs per file; avoids re-computing folder from filepath after the fact.
- **`import os` removed from `parser.py`**: After refactor `scan_maildir` no longer calls `os.walk`, `os.path.*`, or `os.path.join`. Keeping an unused import would be a lint violation and misleading.

## Task 9 — Deterministic Timestamp + PDF Strategy (2026-03-24)

### Decision: `runtime.py` for timestamp handling (not inline in the future PDF builder)
**Choice**: Dedicated `src/maildir_report/runtime.py` module with `parse_report_timestamp()` and `format_report_timestamp()`.
**Reason**: Task 10+ (PDF builder, CLI) will need to pass a timestamp around. Centralising parse/format in `runtime.py` makes it trivially importable from CLI, PDF builder, and manifest writer without duplicating the UTC-normalisation logic. Also makes the "no datetime.now()" contract explicit and enforceable via module boundary.

### Decision: Reject date-only strings, not silently accept them
**Choice**: `"2024-03-20"` (no time component) raises `ValueError`.
**Reason**: Accepting a date-only string would produce a midnight-UTC timestamp silently. This is a footgun: a user who forgets the time component gets a "valid" but misleading timestamp. Fail loudly so the caller is forced to be explicit about the time-of-day.

### Decision: `pdf_determinism.py` is a pure configuration module (no factory/class)
**Choice**: Single function `configure_deterministic_pdf() -> None` that sets `rl_config.invariant = True`.
**Reason**: There is no PDF state to encapsulate. The ReportLab flag is process-global. A factory class or context manager would add boilerplate for zero benefit. Task 10 just needs to call `configure_deterministic_pdf()` once before building any canvas — no other API is needed.

### Decision: `__init__.py` not modified
**Choice**: Neither `runtime` nor `pdf_determinism` are exported from `maildir_report/__init__.py`.
**Reason**: The package's `__init__.py` currently only exports `__version__`. Adding bulk re-exports would require updating it on every new module. Task 10 will import these modules directly (`from maildir_report.runtime import ...`), which is already established as the import pattern across all other modules in this package.

## Task 10 — German PDF Layout Design Decisions (2026-03-24)

### Decision: Helvetica (Type1) over DejaVuSans (TTF) for umlaut support
**Choice**: Use Helvetica with WinAnsiEncoding (built-in ReportLab Type1 font) instead of registering an external TTF like DejaVuSans.
**Reason**: WinAnsiEncoding (ISO-8859-1 extended) covers all German special characters: ä, ö, ü, Ä, Ö, Ü, ß. TTF registration requires a font file at a known path (nix store hash-pinned path is fragile across rebuilds). Built-in Helvetica requires no file path, no `registerFont()` call, and works in any devenv. The tradeoff is that Helvetica does not cover Unicode beyond WinAnsi (e.g., CJK characters), but the report is German-only — this is not a limitation for the task scope.

### Decision: `_extract_pdf_text()` helper in tests instead of pdfminer
**Choice**: Implement a `_extract_pdf_text()` helper in the test file that decodes PDF content streams directly (ASCII85 + zlib + octal-escape decoding).
**Reason**: `pdfminer` is not available in the devenv venv. The helper covers 100% of ReportLab Platypus/canvas output because ReportLab consistently uses `ASCII85Decode + FlateDecode` for content streams, and text operands use the `(text) Tj` format. Octal escapes for Latin-1 chars are deterministic and well-defined. This approach is self-contained, has no new dependencies, and is fast (no subprocess, no disk I/O).

### Decision: `build_report_pdf()` signature keeps dup_groups as a parameter
**Choice**: Accept `dup_groups` even though Task 10 does not render a duplicate-group section yet.
**Reason**: Task 11 (duplicate section) adds that section to the same PDF. Keeping `dup_groups` in the signature now ensures Task 11 doesn't need to change the API — it just adds a new section using the already-available data. The parameter is used in the Zusammenfassung count now and will be used by the full section in Task 11.

### Decision: `__init__.py` not modified
**Choice**: `build_report_pdf` is NOT exported from `maildir_report/__init__.py`.
**Reason**: Consistent with the established import pattern (Tasks 3–9): all modules are imported directly (`from maildir_report.pdf import build_report_pdf`). The CLI (Task 14) will import it the same way. Bulk re-exports from `__init__.py` would require updating it on every new module.

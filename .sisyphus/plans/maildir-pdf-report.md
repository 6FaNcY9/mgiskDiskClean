# Maildir German PDF Report (Deterministic, Audited)

## TL;DR
> Replace the current HTML+nginx review workflow with a deterministic German PDF report plus a machine-readable manifest + decisions template.
> Strip serving/tunnel components entirely. Enforce strict correctness: if any mail file cannot be parsed, fail the run.

**Deliverables**:
- Deterministic German PDF: minimal per-email overview + compact duplicate-group section
- Machine-readable manifest (JSON) that reconciles 1:1 with scanned files
- Editable decisions template (CSV or JSON) keyed by stable IDs
- Updated `devenv.nix` commands for the new workflow (no nginx/cloudflared)

**PDF engine default**: ReportLab (prioritize determinism)

**Estimated Effort**: Large
**Parallel Execution**: YES (4 waves)
**Critical Path**: parsing+inventory invariants -> dedup engine -> PDF generator -> outputs+CLI -> strip serving

---

## Context

### Original Request
- “Strip the serving part, create a PDF at the end in German, structured fast to understand.”
- “100% correct: design logic so it’s impossible to miss anything or be wrong.”
- “Duplicates should be compacted together and shown in the report (shrunken down).”

### Confirmed Decisions
- **Decision unit**: email-level (Maildir message/file)
- **PDF determinism**: byte-for-byte reproducible
- **Strictness**: fail the run if any mail file cannot be parsed cleanly
- **PDF density**: minimal per email in main section
- **Decision capture**: PDF + manifest + editable decisions template
- **Tests**: YES, TDD with pytest

### Repo References (current state)
- Parsing + duplicate grouping pattern: `scripts/maildir_viewer.py`
- Alternative scanner: `scripts/maildir_scan.py`
- Legacy Python2 scanner: `scripts/maildir_attachments_py2.py`
- Serving stack to remove: `nginx/nginx.conf`, `cloudflared/config.yml`
- Workflow wiring: `devenv.nix`

### Metis Review Highlights (applied)
- No silent drops: remove thresholds and replace swallow-and-continue with strict errors
- Determinism guardrails: eliminate `datetime.now()`, sort all walk results, stable IDs, fixed PDF metadata
- Completeness audit: reconcile “files on disk” vs “parsed in manifest” and fail on mismatch
- Duplicate compaction clarity: group header + members table (compact, exhaustive)
- Test fixtures should be synthetic (no PII)

---

## Work Objectives

### Core Objective
Generate a deterministic, audited German PDF report from a Maildir where every scanned mail file is accounted for, duplicates are compacted and exhaustive, and decisions can be captured via a stable-ID template.

### Must NOT Have (Guardrails)
- No nginx/cloudflared serving or replacement web UI
- No silent skipping of mail parts or mail files
- No “best effort” output if any parse errors occur (strict mode)
- No reliance on non-deterministic timestamps or iteration order
- No automated deletion of Maildir content (report + templates only)

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: NO (add pytest)
- **Automated tests**: TDD
- **Framework**: pytest

### Determinism Policy
- Running the generator twice on the same input with the same fixed `--timestamp` MUST yield identical PDF bytes (verified via SHA-256).

### QA Policy (agent-executed)
- Every task includes CLI-executable QA scenarios.
- Evidence saved to `.sisyphus/evidence/` (stdout logs, sha256 sums, sample PDFs, JSON diffs).

---

## Execution Strategy

### Parallel Execution Waves (target 5-8 tasks each)

Wave 1 (foundation + contracts)
Wave 2 (strict audited parsing + dedup correctness)
Wave 3 (deterministic German PDF rendering)
Wave 4 (manifest + decisions template + CLI + strip serving)

---

## TODOs

### Wave 1 — Foundation + Contracts (parallel)

- [x] 1. Add Python test + packaging scaffold (pytest)

  **What to do**:
  - Add a minimal Python project scaffold to support `pytest` and a stable module entrypoint.
  - Ensure tests can run inside `devenv shell`.
  - Add ReportLab dependency for PDF generation.

  **References**:
  - `devenv.nix` — current environment packages and command wiring

  **Acceptance Criteria**:
  - [ ] `devenv shell` provides `python3`, `pytest`, and ReportLab
  - [ ] `pytest -q` runs (may have 0 tests initially) with exit code 0

  **QA Scenarios**:
  ```
  Scenario: Test runner available
    Tool: Bash
    Steps:
      1. Run: devenv shell --command "pytest -q"
    Expected Result: Exit code 0 (runner executes)
    Evidence: .sisyphus/evidence/task-1-pytest.txt
  ```

- [x] 2. Define canonical data model + stable IDs + ordering rules

  **What to do**:
  - Specify (and test) stable identifiers for:
    - Email record (Maildir file)
    - Attachment/part
    - Duplicate group
  - Specify deterministic ordering keys for all lists appearing in PDF/manifest.

  **References**:
  - `scripts/maildir_viewer.py` — current `m["id"] = i` is NOT stable; use as anti-pattern

  **Acceptance Criteria**:
  - [ ] A unit test demonstrates stable ordering independent of filesystem iteration order
  - [ ] A unit test demonstrates stable IDs across repeated scans of identical inputs

  **QA Scenarios**:
  ```
  Scenario: Stable ordering/IDs on rerun
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "stable_id or deterministic_order"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-2-stable-ids.txt
  ```

- [x] 3. Extract and harden Maildir parsing core (strict, no silent skips)

  **What to do**:
  - Reuse parsing patterns from `scripts/maildir_viewer.py` but refactor into a reusable module.
  - Remove/replace thresholds that skip parts (e.g., small part dropping) with explicit categorization.
  - Replace “return None” on parse errors with explicit exceptions (strict mode).

  **References**:
  - `scripts/maildir_viewer.py` — `parse_mail()`, `scan_maildir()` patterns

  **Acceptance Criteria**:
  - [ ] Synthetic fixture Maildir with N files => parser returns exactly N email records
  - [ ] Any unreadable/unparseable file causes a non-zero failure with filepath included

  **QA Scenarios**:
  ```
  Scenario: Strict parsing fails on broken fixture
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "strict_parse"
    Expected Result: PASS (test asserts failure mode + message includes filepath)
    Evidence: .sisyphus/evidence/task-3-strict-parse.txt
  ```

- [x] 4. Implement audited inventory reconciliation (no missing files)

  **What to do**:
  - Define “files on disk” set (Maildir `cur/` + `new/`) and reconcile with parsed records.
  - Fail if mismatch; include missing/extra file lists in error output.

  **References**:
  - `scripts/maildir_viewer.py:176+` — current `os.walk` scanning is not audited

  **Acceptance Criteria**:
  - [ ] Test: fixture with 47 mail files => manifest counters exactly match
  - [ ] Test: fixture with one unreadable file => run fails and names the file

  **QA Scenarios**:
  ```
  Scenario: Inventory reconciliation
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "inventory_reconcile"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-4-inventory.txt
  ```

### Wave 2 — Duplicate Engine + Edge Cases (parallel)

- [x] 5. Upgrade duplicate detection to collision-resistant hashing (SHA-256)

  **What to do**:
  - Replace MD5-based duplicate hashing with SHA-256 for attachment/part content.
  - Ensure hashing covers all relevant parts (no silent exclusions).

  **References**:
  - `scripts/maildir_viewer.py:150-162` — current MD5 hash computation

  **Acceptance Criteria**:
  - [ ] Unit test: identical payloads => identical SHA-256
  - [ ] Unit test: different payloads => different SHA-256

  **QA Scenarios**:
  ```
  Scenario: Hashing invariants
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "sha256"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-5-sha256.txt
  ```

- [x] 6. Define duplicate grouping semantics + compact representation

  **What to do**:
  - Keep current core semantic: emails are grouped if they share at least one duplicate attachment hash.
  - Produce a compact, exhaustive group structure: group header + member list.

  **References**:
  - `scripts/maildir_viewer.py:200-267` — union-find grouping approach

  **Acceptance Criteria**:
  - [ ] Test: 3 emails sharing attachment => exactly 1 group with 3 members
  - [ ] Deterministic “rank/canonical” selection uses stable sort keys (not just date)

  **QA Scenarios**:
  ```
  Scenario: Dedup grouping
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "dedup_group"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-6-dedup-groups.txt
  ```

- [x] 7. Handle nested messages (`message/rfc822`) and related MIME structures

  **What to do**:
  - Ensure forwarded/nested messages are accounted for deterministically.
  - Ensure the report surfaces that a mail contains nested message(s).

  **References**:
  - `scripts/maildir_viewer.py:77-81` — `message/rfc822` currently treated as SKIP_MIME

  **Acceptance Criteria**:
  - [ ] Test fixture includes nested message; parser inventory includes it (or flags it explicitly)

  **QA Scenarios**:
  ```
  Scenario: Nested message handled
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "rfc822"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-7-rfc822.txt
  ```

- [x] 8. Deterministic filesystem traversal + folder naming normalization

  **What to do**:
  - Sort `dirs` and `files` during `os.walk`.
  - Normalize folder naming (Maildir++ dot folders) consistently.

  **References**:
  - `scripts/maildir_viewer.py:179-187` — current walk without sorting

  **Acceptance Criteria**:
  - [ ] Test demonstrates stable scan ordering regardless of filesystem order

  **QA Scenarios**:
  ```
  Scenario: Walk determinism
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "walk_deterministic"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-8-walk.txt
  ```

### Wave 3 — Deterministic German PDF (parallel)

- [x] 9. Implement deterministic timestamp + PDF metadata strategy

  **What to do**:
  - Add `--timestamp` input used everywhere instead of `datetime.now()`.
  - Ensure PDF metadata does not include non-deterministic fields.

  **References**:
  - `scripts/maildir_viewer.py:609` — current `datetime.now()` used for “generated”

  **Acceptance Criteria**:
  - [ ] Test: same input + same timestamp => identical PDF SHA-256 across two runs

  **QA Scenarios**:
  ```
  Scenario: PDF determinism
    Tool: Bash
    Steps:
      1. Run generator twice on same fixture with fixed timestamp
      2. Compare sha256 sums
    Expected Result: Hashes identical
    Evidence: .sisyphus/evidence/task-9-pdf-determinism.txt
  ```

- [x] 10. Create German PDF layout: minimal per email overview

  **What to do**:
  - PDF sections (German):
    - Deckblatt/Meta
    - Zusammenfassung (counts, sizes)
    - E-Mail-Liste (minimal lines)
  - Ensure umlauts render (embed a font like Noto/DejaVu).
  - Use ReportLab for rendering (avoid HTML-to-PDF engines for determinism).

  **Acceptance Criteria**:
  - [ ] Test extracts PDF text and finds required German headers (e.g., “Betreff”, “Von”, “Datum”, “Anhänge”, “Duplikate”)

  **QA Scenarios**:
  ```
  Scenario: German headers render
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "pdf_german_headers"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-10-german.txt
  ```

- [x] 11. Add compact duplicate-groups section (group header + members table)

  **What to do**:
  - Produce a section listing each duplicate group with member emails (minimal rows).

  **Acceptance Criteria**:
  - [ ] Fixture with duplicates produces a PDF section listing exactly those groups and members

  **QA Scenarios**:
  ```
  Scenario: Duplicates compacted
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "pdf_duplicates"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-11-dups.txt
  ```

### Wave 4 — Outputs + CLI + Strip Serving (parallel)

- [x] 12. Generate audited manifest JSON (reconcilable + checksums)

  **What to do**:
  - Output a JSON manifest with:
    - run metadata (fixed timestamp)
    - file inventory counts
    - stable IDs
    - duplicate groups
    - PDF sha256

  **Acceptance Criteria**:
  - [ ] Schema-level tests validate required keys and reconciliation invariants

  **QA Scenarios**:
  ```
  Scenario: Manifest reconciliation
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "manifest"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-12-manifest.txt
  ```

- [x] 13. Generate editable decisions template keyed by stable IDs

  **What to do**:
  - Produce a CSV (or JSON) template listing each email ID + filepath + empty decision column.
  - Ensure stable ordering matches the PDF ordering.

  **Acceptance Criteria**:
  - [ ] Tests verify template contains one row per email and stable IDs

  **QA Scenarios**:
  ```
  Scenario: Decisions template generated
    Tool: Bash
    Steps:
      1. Run: pytest -q -k "decisions_template"
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-13-decisions-template.txt
  ```

- [x] 14. Add CLI entrypoint (Maildir -> PDF + JSON + CSV)

  **What to do**:
  - Provide a single CLI interface (module or script) with:
    - input path
    - output directory
    - fixed `--timestamp`
    - strict mode on by default

  **Acceptance Criteria**:
  - [ ] `--help` works
  - [ ] End-to-end fixture run produces 3 outputs

  **QA Scenarios**:
  ```
  Scenario: End-to-end generation
    Tool: Bash
    Steps:
      1. Run generator on synthetic fixture with fixed timestamp
      2. Assert outputs exist and PDF sha matches manifest
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-14-e2e.txt
  ```

- [x] 15. Update `devenv.nix` workflow commands (remove serving, keep scan-mailbox)

  **What to do**:
  - Remove `serve-*`, `tunnel-*`, and related nginx/cloudflared wiring.
  - Keep/adjust `scan-mailbox` to rsync then generate PDF/manifest/template.

  **References**:
  - `devenv.nix:18-189` — scripts and commands

  **Acceptance Criteria**:
  - [ ] `devenv shell` shows new commands and no serving/tunnel commands

  **QA Scenarios**:
  ```
  Scenario: Devenv commands updated
    Tool: Bash
    Steps:
      1. Run: devenv shell --command "bash -lc 'type scan-mailbox'"
      2. Run: devenv shell --command "bash -lc 'type serve-start'" (should fail)
    Expected Result: scan-mailbox exists; serve-start not found
    Evidence: .sisyphus/evidence/task-15-devenv.txt
  ```

- [x] 16. Strip serving stack from repo (nginx + cloudflared)

  **What to do**:
  - Remove `nginx/` and `cloudflared/` usage from workflows.
  - Ensure no references remain in README.

  **References**:
  - `nginx/nginx.conf`
  - `cloudflared/config.yml`
  - `README.md` — current “serve-start / tunnel-start” workflow

  **Acceptance Criteria**:
  - [ ] No remaining references to `serve-start`, `tunnel-start`, nginx, cloudflared in docs or devenv scripts

  **QA Scenarios**:
  ```
  Scenario: Serving stack removed
    Tool: Bash
    Steps:
      1. Run: devenv shell --command "bash -lc 'grep -R \"tunnel-start\" -n . || true'"
    Expected Result: No matches
    Evidence: .sisyphus/evidence/task-16-strip-serving.txt
  ```

---

## Final Verification Wave

- [x] F1. Determinism + audit verification
  - Run end-to-end generation twice on the same fixture with fixed timestamp
  - Verify identical PDF SHA-256
  - Verify manifest counters reconcile exactly

- [x] F2. Duplicate correctness verification
  - Verify duplicates are grouped exhaustively
  - Verify PDF duplicate-group section lists same members as manifest

- [x] F3. German readability verification
  - Verify required German headings render and text is not garbled

- [x] F4. Serving stripped verification
  - Verify `devenv` no longer exposes serving/tunnel commands

---

## Commit Strategy

- Commit 1: add pytest + pdf dependency + scaffolding
- Commit 2: extract strict parser + audited inventory
- Commit 3: sha256 + duplicate grouping semantics
- Commit 4: deterministic PDF generator (German)
- Commit 5: manifest + decisions template + CLI
- Commit 6: strip nginx/cloudflared + update README/devenv commands

---

## Success Criteria

- `pytest -q` passes
- Determinism check passes (same fixture + fixed timestamp => identical PDF sha256)
- Strict mode: any unreadable/unparseable file fails with filepath
- Outputs produced: PDF + manifest JSON + decisions template
- No nginx/cloudflared workflow remains

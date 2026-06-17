# Current Project Plan: Maildir Reports + Coworker Review App + Local Cleanup Tool

## TL;DR
> **Summary**: Keep the deterministic Maildir report pipeline, store each downloaded mailbox under a per-mailbox folder in a local main data directory (Maildir + extracted attachments), add a small PHP+MySQL reviewer app (bulk workflows so 100k emails is feasible), and add a separate local Python CLI tool to apply reviewed decisions (quarantine/purge).
> **Deliverables**: Enriched decisions CSV with reviewer context fields; local mailbox folder layout under `data/`; extracted attachments on disk + index; local searchable SQLite index across mailboxes; PHP+MySQL reviewer app with pagination+filters+bulk-apply; secure auth + CSRF with a separate admin account; import/export flows; deployment docs; local Python CLI to apply reviewed decisions; automated QA flows.
> **Effort**: Large
> **Parallel**: YES (4 waves)
> **Critical Path**: local mailbox store layout → decisions schema → importer+DB → auth+CSRF → review UI → attachment export + download → bulk actions → export → local decisions apply tool → deployment hardening

## Context
### Original Request
- “can you design only the plan of the current Project please understand the mission”

### Mission (interpreted from repo state + prior direction)
- Generate deterministic mailbox reports from Maildir scans: `<mailbox>.pdf`, `<mailbox>.manifest.json`, `<mailbox>.decisions.csv`.
- Enable coworkers to review what should be kept/removed efficiently, without manual cross-referencing opaque IDs.

### Repo Reality (grounded)
- Pipeline entrypoint: `src/maildir_report/cli.py` (`main()`, `build_pipeline()`), module entry: `src/maildir_report/__main__.py`.
- PDF builder: `src/maildir_report/pdf.py` (`build_report_pdf()`), table builders `_build_email_liste()` and `_build_duplikate_gruppen()`.
- Manifest: `src/maildir_report/manifest.py` (`build_manifest()`, `validate_manifest_invariants()`).
- Decisions template: `src/maildir_report/decisions_template.py` (`generate_decisions_template()`).
- Output naming: `src/maildir_report/cli.py:_output_filenames()` derives `<mailbox>.pdf`, `<mailbox>.manifest.json`, `<mailbox>.decisions.csv`; `.maildir`/`Maildir` basename uses parent folder name.
- Tests: pytest heavy; E2E in `tests/test_e2e_cli.py`; PDF extraction helpers are regex/stream-based and can see word-wrapped text split across newlines.

### Metis Review (gaps addressed)
- Avoid scope creep into “full app platform”: v1 is **review + export only**, no deletion execution.
- Web app must NOT treat `stable_id` as global; it’s only stable **within a report run** because it hashes (absolute) filepath + message-id (`src/maildir_report/ids.py`).
- Key review state by per-report identifier: default to `report_id = manifest.pdf_sha256`.
- Treat pipeline outputs as **immutable inputs**; store coworker edits in MySQL.

### Oracle Guardrails (applied)
- Keep reports **outside webroot**; serve PDFs/CSVs via PHP after auth.
- Use prepared statements everywhere, CSRF tokens on all POST, strict path allowlisting.
- Keep DB writes short and scoped (single-row UPDATE/UPSERT); avoid long transactions for bulk actions.

## Work Objectives
### Core Objective
Deliver a coworker-friendly, secure review workflow for mailbox cleanup decisions while preserving the deterministic/audited pipeline.

### Deliverables
- Enriched `*.decisions.csv` with display + bulk-review columns (still exportable and deterministic)
- Local mailbox storage under a single main folder (one mailbox per subfolder) with Maildir + extracted attachments
- Local searchable SQLite index across all stored mailboxes (email + attachment metadata; optional text search)
- PHP 8.3 + MySQL reviewer app (FTP deployable; no Composer/framework)
- Import: `*.manifest.json` (for `report_id` + audit) + enriched `*.decisions.csv` (for rows + display fields) into MySQL
- Review UI: list/filter emails; mark `keep|delete|unsure`; add optional note; bulk-apply decisions to filtered sets and duplicate groups
- Attachment access: show attachment names/sizes per email and provide a download/view mechanism without requiring coworkers to open huge PDFs
- Export: reviewed CSV for admin execution (no automated deletion)
- Security: shared-password login, session hardening, CSRF on state-changing actions
- Docs: “how coworkers use it” + “how admin exports and applies decisions”

### Definition of Done (verifiable)
- `PYTHONPATH=src python -m pytest -q` exits 0.
- Local web app QA passes using PHP built-in server + curl scripts (see task QA).
- Import a real mailbox report directory → UI shows expected counts → export CSV matches stable IDs and decisions.

### Must Have
- Determinism preserved for pipeline artifacts.
- Mailboxes stored locally in a stable per-mailbox folder layout (not transient `/tmp`).
- A local index DB that makes it easy to locate a mailbox/email/attachment inside the main folder.
- Web app decisions stored in MySQL keyed by `report_id`.
- Exported reviewed CSV stable ordering matches `sort_emails()` conventions.

### Must NOT Have (guardrails)
- No server-side deletion of Maildir files.
- No file deletion/quarantine actions exposed in the web app (review/export only).
- No automatic destructive “dedup cleanup” during mailbox storage; use quarantine-first and/or attachment-file dedupe only.
- No storing secrets or DB credentials under webroot.
- No PHP frameworks / Composer dependency.
- No trusting uploaded filenames/paths; strict allowlisting only.

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Python tests: pytest (existing).
- PHP verification: run built-in server + `curl` flows; minimal PHP unit scripts; use a local/dev MySQL for verification.
- Evidence: `.sisyphus/evidence/current-project/task-*.txt`

## Execution Strategy
### Parallel Execution Waves
Wave 1: decisions schema + test updates; PHP skeleton + config strategy
Wave 2: SQLite schema + importer + export engine
Wave 3: auth + CSRF + review UI
Wave 4: export + local cleanup tool + deployment hardening + end-to-end QA on real artifacts

### Dependency Matrix (high-level)
- Decisions schema change blocks: importer display, export formatting
- DB schema blocks: importer, review persistence, export
- Auth/CSRF blocks: safe review UI

## TODOs

- [x] 1. Extend `decisions.csv` schema with reviewer context columns

  **What to do**:
  - Update `src/maildir_report/decisions_template.py` to generate a deterministic CSV with these columns (exact order):
    - `stable_id` (existing)
    - `filepath` (existing)
    - `decision` (existing; blank in template)
    - `folder` (from EmailRecord)
    - `date` (display string used in PDF/list; use record date field)
    - `from` (sender email string)
    - `subject` (subject as emitted to PDF; keep truncation policy consistent)
    - `total_size_bytes` (raw mail file size; use `EmailRecord.total_size`)
    - `attachment_count` (count of non-body parts; exclude text/plain + text/html bodies)
    - `attachment_total_bytes` (sum of `PartRecord.size` for attachment parts)
    - `attachment_names` (semicolon-separated attachment filenames; empty when none)
    - `is_duplicate` (`true|false` based on `dup_group_id` presence)
    - `dup_group_id` (empty or 64-hex)
    - `dup_rank` (empty or integer)
  - Keep strict determinism: ordering must be `sort_emails()` order.
  - Update `tests/test_decisions_template.py` to assert new header + selected row fields.
  - Update E2E assertions in `tests/test_e2e_cli.py` that currently hardcode old fieldnames.

  **Must NOT do**:
  - Don’t change stable_id algorithms.
  - Don’t add non-deterministic fields (no “generated_at” inside decisions CSV).

  **Recommended Agent Profile**:
  - Category: `deep` — multi-file coordinated change with tests.
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 2,3 | Blocked By: -

  **References**:
  - Code: `src/maildir_report/decisions_template.py`
  - Ordering: `src/maildir_report/ordering.py:sort_emails`
  - E2E patterns: `tests/test_e2e_cli.py`

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python -m pytest tests/test_decisions_template.py -q` exits 0
  - [ ] `PYTHONPATH=src python -m pytest tests/test_e2e_cli.py -q` exits 0
  - [ ] Evidence saved: `.sisyphus/evidence/current-project/task-1-decisions-schema.txt`

  **QA Scenarios**:
  ```
  Scenario: Decisions CSV contains enriched columns
    Tool: Bash
    Steps: Run existing E2E scan fixture that generates decisions CSV; parse CSV header
    Expected: Header matches exact column order; decision column empty in all rows
    Evidence: .sisyphus/evidence/current-project/task-1-decisions-schema.txt

  Scenario: Duplicate email rows show duplicate fields
    Tool: Bash
    Steps: Run E2E fixture with duplicates; inspect one dup row
    Expected: is_duplicate=true, dup_group_id non-empty, dup_rank integer
    Evidence: .sisyphus/evidence/current-project/task-1-decisions-schema-dup.txt
  ```

  **Commit**: YES | Message: `feat(decisions): add reviewer context columns` | Files: `src/maildir_report/decisions_template.py`, `tests/test_decisions_template.py`, `tests/test_e2e_cli.py`

- [x] 2. Add PHP 8.3 + MySQL dev environment wiring

  **What to do**:
  - Update `devenv.nix` to add PHP 8.3 and MySQL tooling needed for local dev.
  - Define a stable local data root (decision-complete default): `$DEVENV_ROOT/data/`.
  - Define per-mailbox folder layout (decision-complete):
    - `$DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir/` (rsync target)
    - `$DEVENV_ROOT/data/mailboxes/<mailbox>/reports/` (pipeline outputs)
    - `$DEVENV_ROOT/data/mailboxes/<mailbox>/attachments/` (extracted attachment files)
    - `$DEVENV_ROOT/data/mailboxes/<mailbox>/index.sqlite` (optional per-mailbox index; see Task 3+4)
  - Add devenv scripts (exact names) to make local workflows one-command:
    - `db-start`: start a local dev MySQL-compatible server (decision-complete: MariaDB) and create the app schema/user for local QA.
    - `db-migrate`: run the web app migrations against the configured MySQL.
    - `store-mailbox <mailbox>`: rsync into the mailbox folder (NOT `/tmp`), then run the local pre-store dedup step (quarantine-first), then run the pipeline to generate `*.pdf/*.manifest.json/*.decisions.csv` into that mailbox’s `reports/`.
    - `index-mailbox <mailbox>`: (re)build the mailbox’s local index DB from its stored maildir + attachments.
    - `index-all`: (re)build a global index DB across all mailboxes (optional; see Task 3).
    - `review-start`: start the PHP built-in server for local QA (`php -S ... -t web/public`) using a local config pointing at `$DEVENV_ROOT/data/`.
    - `apply-decisions <mailbox> <reviewed_decisions.csv>`: invoke the Task 12 tool against that mailbox’s stored maildir.
  - Automatic dedup requirement (decision-complete): `store-mailbox` runs the pre-store dedup step automatically after rsync succeeds and before indexing/report generation.
  - Scope guardrail (confirmed): the pre-store dedup step modifies **only** the local stored copy under `$DEVENV_ROOT/data/` (no remote/server mailbox changes).
  - Add `web/` skeleton (no frameworks):
    - `web/public/index.php` (single entry)
    - `web/src/` (PHP libs)
    - `web/migrations/`
    - `web/config/local.php.example`
    - `web/data/.gitkeep` (or document external data_dir)
    - `web/scripts/` (curl QA scripts)

  **Must NOT do**:
  - Don’t commit secrets (`local.php`).
  - Don’t perform irreversible deletion automatically during `store-mailbox` (quarantine-only by default).

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — new subtree + config concerns.
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 3,4,5,6 | Blocked By: -

  **References**:
  - Hosting constraints (OWASP refs): see librarian output captured in `.sisyphus/drafts/current-project-plan.md`

  **Acceptance Criteria**:
  - [ ] `devenv shell -- php -v` shows PHP 8.3
  - [ ] `php -l web/public/index.php` exits 0
  - [ ] `devenv shell -- db-start --help` exits 0 and prints usage
  - [ ] `devenv shell -- db-migrate --help` exits 0 and prints usage
  - [ ] `devenv shell -- store-mailbox --help` exits 0 and prints usage
  - [ ] `devenv shell -- index-mailbox --help` exits 0 and prints usage
  - [ ] `devenv shell -- apply-decisions --help` exits 0 and prints usage
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-2-php-skeleton.txt`

  **QA Scenarios**:
  ```
  Scenario: PHP server starts
    Tool: Bash
    Steps: Start `php -S 127.0.0.1:8000 -t web/public` and curl `/`
    Expected: HTTP 200 and body contains "OK" placeholder
    Evidence: .sisyphus/evidence/current-project/task-2-php-skeleton.txt

  Scenario: Missing config fails safely
    Tool: Bash
    Steps: Run without `web/config/local.php`
    Expected: HTTP 500 with non-sensitive error; no stack trace in response
    Evidence: .sisyphus/evidence/current-project/task-2-config-missing.txt
  Scenario: store-mailbox creates correct folder layout
    Tool: Bash
    Steps: Run `store-mailbox testbox` against a tiny fixture rsync source (or mocked local copy); list `$DEVENV_ROOT/data/mailboxes/testbox/`
    Expected: maildir/, reports/, attachments/ directories exist; pipeline outputs are written under reports/
    Evidence: .sisyphus/evidence/current-project/task-2-store-mailbox-layout.txt

  Scenario: store-mailbox runs pre-store dedup automatically
    Tool: Bash
    Steps: Prepare fixture containing duplicates per chosen dedup definition; run `store-mailbox testbox`
    Expected: pre-store dedup runs and produces a quarantine/audit output; `store-mailbox` fails hard if dedup step errors
    Evidence: .sisyphus/evidence/current-project/task-2-store-mailbox-dedup.txt
  ```

  **Commit**: YES | Message: `chore(web): add php mysql skeleton` | Files: `devenv.nix`, `web/**`

- [x] 2a. Local pre-store dedup (automatic after mailbox stored)

  **What to do**:
  - Implement a local-only pre-store dedup step that runs as part of `store-mailbox` (Task 2):
    - Input: `$DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir/`
    - Output: a quarantine folder under `$DEVENV_ROOT/data/mailboxes/<mailbox>/quarantine/` plus an append-only audit log.
  - Default dedup definition (decision-complete, minimal-risk):
    - Email duplicates: byte-identical Maildir message files (same SHA-256 of full file bytes) → quarantine all but one deterministically.
    - Attachment duplicates: dedupe extracted attachment files by content hash (implemented in Task 2b).
  - MUST be non-destructive by default: quarantine-only; hard delete requires an explicit `purge` command and plan_id.
  - Determinism: the same mailbox content must produce the same candidate list ordering and same `candidate_set_hash` across two runs.

  **Must NOT do**:
  - Don’t delete anything on the remote server.
  - Don’t treat “shared attachment hash” as “duplicate email” for pre-store cleanup (too risky for an automatic step).

  **Recommended Agent Profile**:
  - Category: `deep`
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 2b | Blocked By: 2

  **References**:
  - Hashing helper: `src/maildir_report/hash.py`
  - Deterministic ordering: `src/maildir_report/ordering.py:sort_emails` (pattern for stable ordering)

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python -m maildir_report.pre_store_dedup --help` exits 0
  - [ ] Running pre-store dedup twice on the same fixture yields identical `candidate_set_hash`
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-2a-pre-store-dedup.txt`

  **QA Scenarios**:
  ```
  Scenario: Quarantine exact-duplicate emails
    Tool: Bash
    Steps: Create fixture Maildir with two byte-identical message files; run pre-store dedup
    Expected: One file remains in place; the other moved into quarantine; audit log records both; exit 0
    Evidence: .sisyphus/evidence/current-project/task-2a-pre-store-dedup.txt

  Scenario: Idempotent re-run
    Tool: Bash
    Steps: Run pre-store dedup again without changing mailbox
    Expected: No additional moves; tool reports 0 actions; exit 0
    Evidence: .sisyphus/evidence/current-project/task-2a-pre-store-dedup-idempotent.txt
  ```

  **Commit**: YES | Message: `feat(store): add local pre-store dedup quarantine` | Files: `src/maildir_report/**`, `tests/**`, `devenv.nix`

- [x] 2b. Extract attachments to disk + build local index DB (per-mailbox + optional global)

  **What to do**:
  - Implement an attachment extraction step that writes attachment files into:
    - `$DEVENV_ROOT/data/mailboxes/<mailbox>/attachments/`
  - Naming (decision-complete, collision-safe): store attachments by content hash and keep original filenames as metadata:
    - File path: `attachments/<sha256>_<size>.<ext-or-bin>`
    - Store `original_filename`, `mime`, and `stable_id` linkage in SQLite.
  - Build a local SQLite index that makes it easy to find mailbox/email/attachment and map back to disk paths.
    - Per-mailbox DB default: `$DEVENV_ROOT/data/mailboxes/<mailbox>/index.sqlite`
    - Optional global DB: `$DEVENV_ROOT/data/index/mail_index.sqlite` (powers cross-mailbox search)
  - Minimum indexed fields (decision-complete):
    - Email: `mailbox`, `stable_id`, `filepath`, `folder`, `date`, `from`, `subject`, `total_size_bytes`
    - Attachment: `sha256`, `size`, `mime`, `original_filename`, `stored_path`, `email_stable_id`
  - Optional “contents” indexing (default minimal):
    - Index email `text/plain` body up to 50KB (no binary attachment content extraction in v1).
    - [DECISION NEEDED] If you truly need full attachment-content search (PDF/DOCX/etc), that’s a separate large scope.

  **Must NOT do**:
  - Don’t allow attachment filenames to control filesystem paths (avoid traversal).
  - Don’t overwrite existing attachment files with different content.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` (cross-cutting: parser/model + local IO + sqlite + determinism)
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 6,7,8 (UI attachment views) | Blocked By: 1, 2a

  **References**:
  - Mail parsing + parts: `src/maildir_report/parser.py`, `src/maildir_report/models.py:PartRecord`
  - Attachment hashing: `src/maildir_report/hash.py`
  - Decisions CSV enrichment fields: Task 1 (`attachment_names`, sizes)

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python -m maildir_report.extract_attachments --help` exits 0
  - [ ] `PYTHONPATH=src python -m maildir_report.index_mailbox --help` exits 0
  - [ ] Running extractor twice is idempotent (no duplicate files; same index row counts)
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-2b-attachments-index.txt`

  **QA Scenarios**:
  ```
  Scenario: Extract one attachment and index it
    Tool: Bash
    Steps: Use existing pytest fixture mail to create a mailbox with 1 attachment; run extraction + indexing
    Expected: attachment file exists under attachments/; index.sqlite has row linking email stable_id to attachment sha256
    Evidence: .sisyphus/evidence/current-project/task-2b-attachments-index.txt

  Scenario: Search by attachment filename
    Tool: Bash
    Steps: Query sqlite for a known original_filename
    Expected: returns mailbox + email stable_id + stored_path
    Evidence: .sisyphus/evidence/current-project/task-2b-search.txt
  ```

  **Commit**: YES | Message: `feat(store): extract attachments and index mailbox` | Files: `src/maildir_report/**`, `tests/**`, `devenv.nix`

- [x] 3. Define MySQL schema and migration runner keyed by report_id

  **What to do**:
  - Create migrations to establish tables in MySQL:
    - `reports(report_id TEXT PRIMARY KEY, mailbox TEXT, generated_at TEXT, pdf_path TEXT, manifest_path TEXT, decisions_seed_path TEXT)`
    - `emails(report_id TEXT, stable_id TEXT, folder TEXT, date TEXT, sender TEXT, subject TEXT, total_size_bytes INTEGER, is_duplicate INTEGER, dup_group_id TEXT, dup_rank INTEGER, PRIMARY KEY(report_id, stable_id))`
    - `decisions(report_id TEXT, stable_id TEXT, decision TEXT, note TEXT, updated_at TEXT, updated_by TEXT, PRIMARY KEY(report_id, stable_id))`
  - Implement migration runner that runs on every request if schema version behind.
  - Use InnoDB; add indexes for: (report_id, stable_id), (report_id, decision), (report_id, dup_group_id), (report_id, updated_by), and a prefix index for subject/from search.

  **Must NOT do**:
  - Don’t put DB credentials in git.

  **Recommended Agent Profile**:
  - Category: `deep`

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 4,5 | Blocked By: 2

  **References**:
  - PDO MySQL docs: `https://www.php.net/manual/en/ref.pdo-mysql.php`

  **Acceptance Criteria**:
  - [ ] `php web/src/cli/migrate.php` (or equivalent) creates tables in a local/dev MySQL
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-3-migrations.txt`

  **QA Scenarios**:
  ```
  Scenario: Fresh DB initializes
    Tool: Bash
    Steps: Create empty MySQL schema; run migrate; query information_schema
    Expected: required tables exist; schema version recorded
    Evidence: .sisyphus/evidence/current-project/task-3-migrations.txt

  Scenario: Connection failure fails safely
    Tool: Bash
    Steps: Run with invalid DB credentials
    Expected: app returns HTTP 500 with non-sensitive error; no secrets echoed
    Evidence: .sisyphus/evidence/current-project/task-3-connection-fail.txt
  ```

  **Commit**: YES | Message: `feat(web): add sqlite schema and migrations` | Files: `web/src/**`, `web/migrations/**`

- [x] 4. Implement report import (manifest + decisions seed) into SQLite

  **What to do**:
  - Add an admin-only import action that:
    - Reads `<mailbox>.manifest.json` and extracts `report_id = pdf_sha256`, `generated_at`, and email records.
    - Optionally reads `<mailbox>.decisions.csv` (seed) and stores seed path.
    - Inserts/updates `reports` and `emails` rows.
  - Validate manifest schema version; hard-fail on unknown versions.
  - Strict path allowlisting: only import from configured `data_dir` under `reports/<mailbox>/...`.

  **Must NOT do**:
  - Don’t accept arbitrary upload paths; don’t allow path traversal.

  **Recommended Agent Profile**:
  - Category: `deep`

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 5,6 | Blocked By: 3, 1

  **References**:
  - Manifest structure: `src/maildir_report/manifest.py`
  - Report id: `manifest["pdf_sha256"]`

  **Acceptance Criteria**:
  - [ ] Importing a fixture report results in correct email count in DB
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-4-import.txt`

  **QA Scenarios**:
  ```
  Scenario: Import success
    Tool: Bash
    Steps: Start php server; POST admin import for a known report directory
    Expected: HTTP 200; DB contains N emails; report listed in dashboard
    Evidence: .sisyphus/evidence/current-project/task-4-import.txt

  Scenario: Reject unknown manifest schema
    Tool: Bash
    Steps: Modify schema_version in a temp copy; attempt import
    Expected: HTTP 400 with clear message; no partial writes
    Evidence: .sisyphus/evidence/current-project/task-4-import-schema.txt
  ```

  **Commit**: YES | Message: `feat(web): import manifest into sqlite` | Files: `web/src/**`, `web/public/index.php`

- [x] 5. Add authentication + sessions + CSRF for all state changes (coworker + admin)

  **What to do**:
  - Implement two roles:
    - Coworker user: can review/update decisions for a report.
    - Admin user: can see all coworker decisions and can export decisions files.
  - Auth implementation (decision-complete): shared-password login using `password_hash` + `password_verify` for each role.
  - Store only password hashes in `web/config/local.php`:
    - `coworker_password_hash`
    - `admin_password_hash`
  - Session hardening: HttpOnly, Secure, SameSite=Strict; `session_regenerate_id(true)` after login.
  - CSRF synchronizer token pattern; validate with `hash_equals` on all POST routes.
  - Record who made a change (decision-complete): capture a mandatory “display name” at coworker login and store it as `updated_by` / `reviewed_by` on decision updates.
  - Route protection (decision-complete):
    - Admin-only: import actions, export endpoints, and any “all users” dashboards.
    - Coworker-only: review/update endpoints.

  **Must NOT do**:
  - Don’t store plaintext passwords.
  - Don’t allow POST without CSRF.
  - Don’t let coworker sessions access admin endpoints.

  **Recommended Agent Profile**:
  - Category: `unspecified-high`

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 6 | Blocked By: 2,3

  **References**:
  - OWASP Session: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
  - OWASP CSRF: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html

  **Acceptance Criteria**:
  - [ ] Unauthed GET to `/` redirects to login
  - [ ] POST without CSRF returns 403
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-5-auth-csrf.txt`

  **QA Scenarios**:
  ```
  Scenario: Login then access dashboard
    Tool: Bash
    Steps: curl login with correct password; reuse cookie jar; GET dashboard
    Expected: HTTP 200; dashboard HTML contains mailbox/report list
    Evidence: .sisyphus/evidence/current-project/task-5-auth-csrf.txt

  Scenario: CSRF rejection
    Tool: Bash
    Steps: POST decision update without csrf_token
    Expected: HTTP 403
    Evidence: .sisyphus/evidence/current-project/task-5-csrf-reject.txt
  ```

  **Commit**: YES | Message: `feat(web): auth sessions and csrf` | Files: `web/src/**`, `web/public/index.php`

- [x] 6. Build coworker review UI + i18n toggle (German/Ukrainian) + admin overview

  **What to do**:
  - Dashboard: list imported reports (mailbox + generated_at + counts) keyed by `report_id`.
  - Review list page: paginated table of emails with columns: date, from, subject, total size, duplicate info, current decision, note.
  - Filters: decision state, duplicates-only, search substring (subject/from).
  - Attachments (UI only): show `attachment_count` + `attachment_names` and link to attachment access (implemented in Task 7).
  - Bulk decisions (UI hooks only): add entry points for bulk-apply flows (implemented in Task 8).
  - Decision update: keep/delete/unsure + optional note; persist to `decisions` table.
  - i18n: simple translation arrays for UI strings; toggle stored in session/cookie.
  - PDF access: link to stream PDF for that report_id (server-side read only, after auth).
  - Admin overview (minimal): admin can view an aggregated table of decisions for a report, with filters by `decision` and by `updated_by`.

  **Must NOT do**:
  - Don’t try to render the PDF pages into HTML.
  - Don’t expose raw filesystem paths.

  **Recommended Agent Profile**:
  - Category: `visual-engineering` — UI work, but keep simple.
  - Skills: [`frontend-ui-ux`] — for intentional, readable UI.

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 7,8,9,10 | Blocked By: 4,5

  **Acceptance Criteria**:
  - [ ] Review list loads for an imported report and shows correct row count via pagination
  - [ ] Changing a decision persists and reflects on refresh
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-6-review-ui.txt`

  **QA Scenarios**:
  ```
  Scenario: Mark one email as delete
    Tool: Bash
    Steps: Import report; open list; POST update decision with csrf
    Expected: Decision shown as delete; DB row updated_at changes
    Evidence: .sisyphus/evidence/current-project/task-6-review-ui.txt

  Scenario: Language toggle
    Tool: Bash
    Steps: Toggle lang=uk; reload
    Expected: UI labels change language deterministically
    Evidence: .sisyphus/evidence/current-project/task-6-i18n.txt
  ```

  **Commit**: YES | Message: `feat(web): coworker review ui` | Files: `web/public/**`, `web/src/**`, `web/assets/**`

- [x] 7. Attachment access: minimal "view/download attachments" strategy

  **What to do**:
  - Implement attachment access using the extracted attachments produced by Task 2b.
  - Python/local side: attachment files are stored under `$DEVENV_ROOT/data/mailboxes/<mailbox>/attachments/` and indexed in SQLite (Task 2b).
  - PHP side (web app): add an authenticated download route that serves only:
    - the report PDF/seed CSV/manifest
    - attachment files by `(report_id, email_stable_id, attachment_sha256)` looked up from SQLite (never direct paths from user input)
  - Download route must enforce:
    - `report_id` scoping
    - strict allowlisting to configured `data_dir`
    - safe `Content-Type` and `Content-Disposition` headers

  **Must NOT do**:
  - Don’t allow arbitrary path downloads.
  - Don’t expose attachments without auth.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — cross-boundary (Python artifact + PHP streaming) and security-sensitive.
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 8,9,10 | Blocked By: 4,5,6

  **References**:
  - Pipeline output wiring: `src/maildir_report/cli.py:build_pipeline`
  - Email/part structure: `src/maildir_report/models.py` (EmailRecord.parts/PartRecord)
  - Attachment extraction + index: Task 2b
  - Decisions seed fields: Task 1 schema (attachment_* columns)

  **Acceptance Criteria**:
  - [ ] For a known imported report, downloading one attachment returns HTTP 200 and a file with non-zero size
  - [ ] Attempting to download with a mismatched `report_id` returns HTTP 404
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-7-attachments.txt`

  **QA Scenarios**:
  ```
  Scenario: Download attachment for a row
    Tool: Bash
    Steps: Import report; request download for one `(stable_id, attachment_sha256)` using auth cookie
    Expected: HTTP 200; Content-Disposition uses the original filename; bytes match expected size from index
    Evidence: .sisyphus/evidence/current-project/task-7-attachments.txt

  Scenario: Path traversal rejected
    Tool: Bash
    Steps: Try to request a download with `../` or absolute path injection in parameters
    Expected: HTTP 400/404; no file contents leaked
    Evidence: .sisyphus/evidence/current-project/task-7-attachments-traversal.txt
  ```

  **Commit**: YES | Message: `feat(web): attachment access` | Files: `web/src/**`, `web/public/index.php`, (optional) `src/maildir_report/**`

- [x] 8. Bulk decision workflows (make 100k-email review feasible)

  **What to do**:
  - Implement bulk-apply actions to avoid manual per-row clicking:
    - Bulk apply decision to current filtered set (with a confirmation step)
    - Duplicate-group workflow: show dup groups (by `dup_group_id`, total_size_bytes) and allow “keep canonical / delete rest” in one click
    - “Top savings” workflow: sort by `total_size_bytes` and/or `attachment_total_bytes`, allow batch selection
  - Ensure bulk operations are idempotent and scoped to `report_id`.
  - Record audit fields on updates: updated_at, reviewed_by (from session).

  **Must NOT do**:
  - Don’t run long transactions; update in batches.
  - Don’t allow bulk apply without CSRF and explicit confirmation.

  **Recommended Agent Profile**:
  - Category: `deep` — SQL + UI + correctness.
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 9,10 | Blocked By: 4,5,6

  **References**:
  - Duplicate semantics: `src/maildir_report/dedup.py` (dup_group_id, dup_rank)
  - Ordering: `src/maildir_report/ordering.py` (stable review ordering)

  **Acceptance Criteria**:
  - [ ] Bulk apply updates >100 rows in one request without timeout (local dev)
  - [ ] Duplicate-group “keep canonical / delete rest” results in exactly one keep and N-1 delete per group
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-8-bulk.txt`

  **QA Scenarios**:
  ```
  Scenario: Bulk apply to filtered set
    Tool: Bash
    Steps: Filter duplicates-only; bulk apply decision=delete; then export and count decision=delete rows
    Expected: Count increases by expected filtered size; non-filtered rows unchanged
    Evidence: .sisyphus/evidence/current-project/task-8-bulk.txt

  Scenario: Duplicate group action
    Tool: Bash
    Steps: Pick one dup_group_id; run group action
    Expected: DB shows one keep (dup_rank=0) and remaining delete for that group
    Evidence: .sisyphus/evidence/current-project/task-8-dup-group.txt
  ```

  **Commit**: YES | Message: `feat(web): bulk review actions` | Files: `web/src/**`, `web/public/index.php`, `web/assets/**`

- [x] 9. Admin: export reviewed decisions CSV for local execution

  **What to do**:
  - Provide admin-only export endpoints for a `report_id`:
    - `decisions.reviewed.csv` (machine-friendly): outputs CSV matching the seed decisions schema (Task 1 columns), with `decision` filled from DB.
    - `decisions.audit.csv` (human/audit): same rows plus extra columns at end: `updated_by`, `updated_at`, `note`.
  - The local deletion tool (Task 12) consumes `decisions.reviewed.csv`.
  - `decisions.reviewed.csv` must output CSV with:
    - Same columns as the seed decisions CSV (Task 1 header), with `decision` filled from DB.
    - Stable row ordering matching `emails` table ordering (date then filepath-like deterministic key from manifest; if filepath not stored, store it).
  - Include a banner row or separate metadata file (preferred) describing `report_id` and generated_at (avoid making CSV parsing weird).

  **Must NOT do**:
  - Don’t mutate seed CSV in-place.

  **Recommended Agent Profile**:
  - Category: `deep`

  **Parallelization**: Can Parallel: YES | Wave 4 | Blocks: 10 | Blocked By: 6,7,8

  **Acceptance Criteria**:
  - [ ] Exported CSV parses in Python `csv` and has decision values for edited rows
  - [ ] Stable IDs set equals manifest email_stable_ids
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-9-export.txt`

  **QA Scenarios**:
  ```
  Scenario: Export CSV contains decisions
    Tool: Bash
    Steps: Mark 2 decisions; download export; parse with python
    Expected: Those stable_ids have decision values; others empty/unsure as configured
    Evidence: .sisyphus/evidence/current-project/task-9-export.txt

  Scenario: Export rejects unknown report_id
    Tool: Bash
    Steps: Request export for fake report_id
    Expected: HTTP 404
    Evidence: .sisyphus/evidence/current-project/task-9-export-404.txt
  ```

  **Commit**: YES | Message: `feat(web): export reviewed decisions csv` | Files: `web/src/**`, `web/public/index.php`

- [x] 10. Deployment hardening (FTP) + operational docs

  **What to do**:
  - Add `web/public/.htaccess` (if Apache) to rewrite to index.php and deny direct access to sensitive paths.
  - Document required directory layout:
    - webroot contains only `web/public/**` (index + assets)
    - data_dir and sqlite db path outside webroot
  - Add security headers (as best-effort in PHP): `Content-Security-Policy` baseline, `X-Frame-Options`, `X-Content-Type-Options`.
  - Add a “maintenance mode” file check to avoid half-upload broken states.

  **Recommended Agent Profile**:
  - Category: `devops`

  **Parallelization**: Can Parallel: YES | Wave 4 | Blocks: - | Blocked By: 6,7,8,9

  **Acceptance Criteria**:
  - [ ] Docs in `README.md` explain coworker flow + admin export flow + FTP deploy steps
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-10-deploy-docs.txt`

  **QA Scenarios**:
  ```
  Scenario: Maintenance mode
    Tool: Bash
    Steps: Create maintenance flag; curl app
    Expected: HTTP 503 with friendly message
    Evidence: .sisyphus/evidence/current-project/task-10-maintenance.txt

  Scenario: Direct access blocked (best-effort)
    Tool: Bash
    Steps: Request a known non-public path
    Expected: HTTP 404/403
    Evidence: .sisyphus/evidence/current-project/task-10-block.txt
  ```

  **Commit**: YES | Message: `docs(deploy): ftp hardening and workflow` | Files: `README.md`, `web/public/.htaccess`, `web/src/**`

- [x] 11. (Optional) IMAP ingestion via `imap-tools` (replace rsync Maildir fetch)

  **Why/when**:
  - Use this only if rsync access is unavailable or you want to pull mailboxes from providers that only expose IMAP.
  - This does NOT change review flows; it only changes how raw messages arrive locally.

  **What to do**:
  - Add an alternate ingestion command that downloads messages from IMAP and materializes a **local Maildir** so the existing pipeline can run unchanged.
  - Use Python `imap-tools` to fetch messages and write each message as a single RFC822 `.eml` file inside Maildir `cur/`.
  - Scope is READ-ONLY: do not move/delete/flag messages on the server.
  - Security + config (decision-complete defaults):
    - Require TLS (IMAPS port 993).
    - Credentials via env vars only: `IMAP_SERVER`, `IMAP_USER`, `IMAP_PASS` (prefer app password).
    - Never accept password via CLI args.
  - Determinism (decision-complete filename scheme):
    - Resolve `UIDVALIDITY` once per folder and incorporate it into filenames.
    - Save each message to `.../Maildir/cur/{uidvalidity}.{uid}.eml`.
    - Re-running ingestion overwrites the same filename (idempotent) and does not create duplicates.
  - Folder selection (decision-complete default): INBOX only for v1; later can add allowlisted folders.
  - Search criteria (decision-complete default): `ALL` (historical backfill) with an optional `--since YYYY-MM-DD` filter.
  - Integrate with `scan-mailbox` workflow as a selectable source:
    - default `source=rsync` (current)
    - optional `source=imap` (new)
  - Output layout (decision-complete default):
    - IMAP materialization path: `data_dir/imap/<mailbox>/INBOX/Maildir/`
    - Then run pipeline on that Maildir path.

  **Must NOT do**:
  - Don’t implement server-side deletion/moves/flag changes in v1.
  - Don’t store plaintext passwords in repo.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — network IO + credentials + determinism.
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 1..10? | Blocked By: -

  **References**:
  - Existing acquisition expectation: `README.md` (`scan-mailbox <mailbox>` currently rsyncs)
  - Pipeline input contract: `src/maildir_report/cli.py:build_pipeline` (expects Maildir path)
  - Parser accepts RFC822 bytes: `src/maildir_report/parser.py` (reads raw bytes)
  - Library: https://pypi.org/project/imap-tools/ (imap-tools)
  - IMAP RFC: https://www.rfc-editor.org/rfc/rfc3501

  **Acceptance Criteria**:
  - [ ] IMAP ingest produces a local Maildir with `cur/` containing deterministic `{uidvalidity}.{uid}.eml` files
  - [ ] Running ingest twice produces the same file list (no duplicates) and the pipeline scan output is unchanged
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-11-imap.txt`

  **QA Scenarios**:
  ```
  Scenario: IMAP ingest (read-only) then scan
    Tool: Bash
    Steps: Use a test IMAP account; ingest a small mailbox; run scan pipeline on the resulting local dir
    Expected: 3 artifacts produced; no server-side mutations; deterministic `.eml` paths across two ingests
    Evidence: .sisyphus/evidence/current-project/task-11-imap.txt
  ```

  **Commit**: YES | Message: `feat(ingest): add optional imap source` | Files: `src/maildir_report/**`, `devenv.nix`, `README.md`

- [x] 12. Local Python CLI tool: apply reviewed decisions file (delete/quarantine local emails)

  **What to do**:
  - Add a standalone Python CLI tool that operates on your **local** Maildir copy (rsync/IMAP materialized) and applies the admin-exported reviewed decisions file.
  - Scope (decision-complete): for each decisions row, if `decision` is `delete` (case-insensitive), quarantine (default) or delete (explicit flag) the mail file at `filepath`.
  - Attachments handling (decision-complete): attachments are part of the mail file; deleting/quarantining the message file deletes/quarantines its attachments automatically.
  - Inputs (decision-complete):
    - `--maildir-root <path>`: Maildir root directory (must contain `cur/` and/or `new/`).
    - `--decisions-csv <path>`: the admin-exported `decisions.reviewed.csv`.
    - `--mode quarantine|delete` (default: `quarantine`).
  - Safety rails (decision-complete):
    - Always support `--dry-run` that prints totals and writes a plan file.
    - Preview-then-execute workflow using a plan artifact:
      1) `plan` command writes `cleanup_plan.json` containing candidate file list, sizes, `candidate_set_hash`, and an expiry timestamp.
      2) `apply` command requires `--plan cleanup_plan.json --confirm <candidate_set_hash_prefix>` and refuses if hash/expiry mismatches.
    - Hard allowlisting: every candidate filepath must resolve under `maildir_root` (no symlinks escaping, no `..`).
    - Default caps: refuse to act on >10,000 files unless `--break-glass` is explicitly set.
    - Idempotency: re-applying the same plan is safe (already-quarantined files are skipped with a clear note).
  - Quarantine strategy (decision-complete):
    - Move files (rename) into `<maildir_root>/.quarantine/<plan_id>/(cur|new)/<original_filename>`.
    - Write an append-only log `<maildir_root>/.quarantine/<plan_id>/audit.jsonl` with one record per moved file.
    - Provide a `restore` command that can restore from a plan_id by moving files back to their recorded original paths.
  - Purge strategy (decision-complete):
    - Provide a `purge` command that deletes quarantined files only, requires `--confirm` and supports `--dry-run`.
    - Default retention window: tool refuses purge unless the plan_id directory is older than 7 days (override via `--force`).

  **Must NOT do**:
  - Don’t delete anything from the web app.
  - Don’t attempt MIME rewriting to “remove attachments only”.
  - Don’t talk to IMAP for deletion in v1.

  **Recommended Agent Profile**:
  - Category: `deep` — correctness + filesystem safety + tests.
  - Skills: []

  **Parallelization**: Can Parallel: YES | Wave 4 | Blocks: - | Blocked By: 1

  **References**:
  - Email fields: `src/maildir_report/models.py:EmailRecord` (filepath)
  - Decisions CSV generator: `src/maildir_report/decisions_template.py` (Task 1 extends schema)
  - Safety design notes (oracle): dry-run, plan_id, quarantine -> purge lifecycle

  **Acceptance Criteria**:
  - [ ] `PYTHONPATH=src python -m pytest -q` exits 0
  - [ ] `PYTHONPATH=src python -m maildir_report.apply_decisions --help` exits 0
  - [ ] Evidence: `.sisyphus/evidence/current-project/task-12-apply-decisions.txt`

  **QA Scenarios**:
  ```
  Scenario: Dry-run produces stable plan
    Tool: Bash
    Steps: Create a tmp Maildir fixture with 2 messages; create a reviewed decisions CSV marking one row as delete; run `plan` twice
    Expected: Both runs produce identical candidate_set_hash and identical candidate list ordering
    Evidence: .sisyphus/evidence/current-project/task-12-apply-decisions.txt

  Scenario: Apply quarantines only decision=delete
    Tool: Bash
    Steps: Apply the plan; list maildir cur/new
    Expected: Only files marked delete are moved under .quarantine/<plan_id>/; non-delete rows unchanged; audit.jsonl records each move
    Evidence: .sisyphus/evidence/current-project/task-12-apply.txt

  Scenario: Path traversal rejected
    Tool: Bash
    Steps: Tamper plan file to include a path outside maildir_root; run apply
    Expected: Tool refuses with non-zero exit; no files moved
    Evidence: .sisyphus/evidence/current-project/task-12-traversal.txt
  ```

  **Commit**: YES | Message: `feat(cleanup): add local decisions apply tool` | Files: `src/maildir_report/**`, `tests/**`, `README.md` (if needed)

## Final Verification Wave (MANDATORY)
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- One commit per TODO item above; do not mix pipeline schema changes with web app changes.

## Defaults Applied (to avoid blocking decisions)
- Web app v1 uses shared password auth (not per-user accounts); records `reviewed_by` as a freeform display name.
- Review workflow is export-only in the web app; deletions are executed locally via the Task 12 CLI tool (not on the server).
- Web UI joins manifest-derived email metadata; enriched decisions CSV is produced anyway for portability.
- v1 acquisition remains rsync/Maildir; IMAP ingestion is optional and out of critical path.

## Decisions Needed (can be revised later without blocking v1)
- Whether to store report artifacts under `reports/<mailbox>/...` vs flat `reports/` (plan assumes subfolders for multi-mailbox).
- Whether to allow uploads via the web app (plan assumes **no uploads**; import reads from server-local `data_dir`).
- Whether you want IMAP ingestion at all (plan defaults to **no** for smallest v1).
- Review ergonomics default: whether the UI should assume “keep by default” and only explicitly mark deletes (plan recommends **yes** for 100k-scale review).

# Current Project Decisions

## Task 1 — Schema design choices (2026-03-26)

- `attachment_count` counts ALL items in `rec["parts"]` (not just items with real filenames). Rationale: the parser already excludes body-only parts, so every part in the list is an attachment or inline non-body part — all relevant to reviewer.
- `is_duplicate` uses `"true"/"false"` lowercase strings (not `True/False` booleans) because the entire row is string-valued for CSV/JSON simplicity.
- `from` key (not `sender`) used in CSV header to match reviewer expectations; `sender` field from EmailRecord is the source.
- `dup_rank=0` serialises as `"0"` (not empty) — canonical member of a dup group still has a rank.
- No truncation applied to `subject` in this CSV (unlike PDF which may word-wrap); raw subject string preserved for reviewer searchability.

## Task 2 — Design choices (2026-03-26)

- **MariaDB over MySQL**: Used `pkgs.mariadb` (devenv default) for MySQL-compatible local dev. DSN uses `mysql:` PDO driver which works with both.
- **Socket auth for local dev**: `services.mysql.ensureUsers` creates user `mailreview` with no password; socket auth is safe and requires no hardcoded credentials.
- **Graceful dedup skip**: `store-mailbox` checks for the pre_store_dedup module before calling it. This avoids blocking Task 2 pipeline tests until Task 2a is implemented, while keeping the hook wiring in place.
- **No .gitignore for local.php**: Left to the developer to add; the example file makes the pattern clear. `web/config/local.php` should be in `.gitignore` — added as a reminder in `local.php.example`.
- **reports/ naming**: The pipeline names outputs from the Maildir basename. Since rsync target is `maildir/.maildir/`, outputs are `maildir.*`. This is a consequence of the existing CLI design and acceptable for v1.
- **web/data/.gitkeep**: Tracks the `data/` directory for web app use (e.g. maintenance flag) without putting it in webroot layout confusion.
- **Autoloader in index.php**: Simple `spl_autoload_register` using `MailReview\` prefix maps to `web/src/`; no Composer needed per plan constraint.

## Task 3 — Schema design choices (2026-03-26)

- **`decisions.decision` as ENUM not TEXT**: Using `ENUM('keep','delete','unsure','')` constrains the values at the DB level; empty string `''` represents "unset" decision. This avoids NULLs and makes filtering clean (`WHERE decision = 'delete'`).
- **`emails.dup_rank = -1` default**: Sentinel value `-1` chosen over NULL for ease of PHP comparison; `dup_rank >= 0` indicates duplicate membership. `dup_rank = 0` is canonical.
- **Prefix indexes instead of full-text**: `subject(64)` and `sender(128)` prefix indexes chosen for v1 over FULLTEXT. FULLTEXT requires `FULLTEXT INDEX` declaration and `MATCH AGAINST` syntax which complicates the query layer. Prefix indexes support `LIKE 'foo%'` patterns cheaply. If full substring search is needed later, FULLTEXT can be added in a subsequent migration.
- **`schema_migrations` PK is filename**: Migration filename (`001_initial_schema.sql`) used as PK rather than integer — self-describing, gap-safe, and prevents re-applying renamed files accidentally.
- **No separate schema version integer**: Chose filename-based tracking over a single `schema_version` integer. Easier to reason about in a multi-migration scenario without managing a counter.

## Task 4 — Import design choices (2026-03-26)

- **`report_name` parameter defaults to `'maildir'`**: store-mailbox always rsyncs to `maildir/.maildir/` producing `maildir.manifest.json`. The importer accepts an optional `report_name` override in the POST body for flexibility, defaulting to `'maildir'`. No change to pipeline needed.
- **Route `POST /admin/import` with no session auth in Task 4**: Auth/CSRF deferred to Task 5. The route is POST-only with JSON body validation as a minimal guard. Task 5 will add session auth + CSRF token on top.
- **`GET /admin/reports` added as companion listing endpoint**: Lightweight JSON list of all imported reports. Needed by Task 5+ UI. No pagination in Task 4 (LIMIT 100 placeholder).
- **Decisions CSV is authoritative for display fields, manifest is authoritative for dup info**: When both exist, manifest dup_groups overrides CSV dup fields for consistency. CSV `folder`/`date`/`sender`/`subject`/`size` are always from CSV.
- **`import-report` devenv script wraps curl POST**: CLI convenience that calls the API. Requires review-start to be running. No direct DB writes — goes through same import flow as the web UI.

## Task 5 — Auth/Session/CSRF design choices (2026-03-26)

- **Single shared-password per role (not per-user accounts)**: Matches plan requirement. Coworker identity is captured as a freeform display name at login and stored in session + decision records. No user table needed in v1.
- **display_name mandatory for coworker, optional for admin**: Coworkers must provide a name to enable `updated_by` attribution on decisions. Admin has no such requirement since their role is import/export, not per-row decision work.
- **CsrfGuard token NOT rotated on each validate**: Token rotates only on login/logout (via `session_regenerate_id`). Rotating per-POST breaks multi-tab use (second tab's form has stale token). OWASP supports this pattern.
- **CSRF via POST field `csrf_token` OR header `X-CSRF-Token`**: Header variant supports AJAX/JSON flows where form encoding is inconvenient. Both are checked in `CsrfGuard::enforce()`.
- **`/logout` requires CSRF**: Even though logout is "destructive in a good way", CSRF-triggered logout (forcing the user offline) is a denial-of-service attack vector. CSRF on logout is correct per OWASP.
- **`POST /review/update` scoped to coworker only**: Admin cannot update decisions (their role is export/overview). This enforces a clean separation: coworkers review, admin exports. Relaxing later is a one-line config change.
- **Route `/` serves dashboard HTML**: For Task 5 scope, the dashboard is a minimal HTML page confirming role/name. Full review UI comes in Task 6.
- **QA passwords `coworker123` / `admin123` stored as bcrypt in `local.php`**: These are dev-only credentials; `local.php` is gitignored. The `local.php.example` shows the pattern for production (where the operator generates their own hashes).

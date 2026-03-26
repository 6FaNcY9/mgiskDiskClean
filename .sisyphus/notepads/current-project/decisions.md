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

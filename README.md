# mgiskDiskClean

Self-contained devenv for mrija.org mailbox disk cleanup.
Scans Maildir attachments and generates a PDF report for the owner to review.

## Structure

```
mgiskDiskClean/
  devenv.nix          # devenv shell with all packages + commands
  devenv.yaml         # nixpkgs input pins
  reports/            # generated reports (not in git)
  scripts/
    maildir_attachments_py2.py   # scanner script
  logs/               # logs (generated, not in git)
```

## Setup

```bash
# 1. enter devenv
devenv shell

# 2. scan a mailbox and generate reports
scan-mailbox <mailbox>
#   e.g. scan-mailbox gabriel.hangel

# 3. find outputs in reports/
ls reports/
```

## Workflow for each mailbox

```bash
# scan mailbox — rsyncs maildir locally, generates PDF + manifest + decisions
scan-mailbox <mailbox>

# outputs written to reports/
#   report.pdf       — German PDF report
#   manifest.json   — audited JSON manifest (includes PDF SHA-256)
#   decisions.csv   — editable keep/delete decisions template
```

## Commands

| Command                | Description                                               |
|------------------------|-----------------------------------------------------------|
| `scan-mailbox <name>`  | Rsync maildir from server, generate PDF/manifest/decisions |

## Notes

- Reports are written to `reports/` — do NOT commit that directory to git
- Do NOT commit `logs/` to git


---

## Deployment & Operations

### Project Overview

Mailbox Review App is a self-contained PHP 8.3 + MySQL web application for reviewing email cleanup decisions. A Python pipeline scans a Maildir, generates a PDF report and a decisions CSV; these are imported into MySQL. Coworkers then log in to review each email (keep/delete/unsure), and an admin exports the reviewed CSV to apply deletions offline. No Composer, no framework — designed to be uploaded to shared hosting via FTP.

### Directory Layout

```
mrijaPageClean/
├── devenv.nix              # devenv shell with all packages + commands
├── devenv.yaml             # nixpkgs input pins
├── src/                    # Python pipeline (maildir_report package)
├── tests/                  # Python pytest tests
├── reports/                # generated PDF/manifest/decisions (not in git)
├── data/                   # local mailbox store (not in git)
│   └── mailboxes/<name>/   # per-mailbox: Maildir + attachments + index.sqlite
├── logs/                   # Python + PHP logs (not in git)
└── web/
    ├── public/             # ← Apache/PHP webroot (deploy this via FTP)
    │   ├── index.php       # single entry point
    │   ├── login.php       # login page
    │   └── .htaccess       # Apache rewrite rules + security
    ├── config/
    │   ├── local.php.example
    │   └── local.php       # created by you; NEVER commit
    ├── src/                # PHP class library (not served directly)
    ├── migrations/         # SQL schema migrations
    ├── scripts/            # QA + utility shell scripts
    ├── maintenance.flag    # create to trigger 503 maintenance mode
    └── index.php           # top-level catch-all (redirects to public/)
```

### Coworker Review Flow

1. **Log in** at `https://<host>/` with the coworker password (shared secret).
2. On the **Dashboard**, click **View Reports** to see imported mailboxes.
3. Select a mailbox → the review table appears with filters (decision, duplicates, search).
4. For each email: pick a decision from the dropdown (`keep` / `delete` / `unsure`) and optionally add a note.
5. Changes save automatically via AJAX (no page reload needed).
6. Use the **Duplicates** filter to find duplicate groups; bulk-decision them if needed.
7. When done, notify the admin that the review is complete.

### Admin Export Flow

1. **Log in** at `https://<host>/` with the admin password.
2. Go to **Admin Overview** to see all decisions across reviewers.
3. To export, send a request to:
   ```
   GET /admin/export/decisions?report_id=<report_id>
   ```
   (or use the export button in the admin UI — logged in as admin).
4. Save the CSV file. The columns include: `stable_id`, `decision`, `note`, `updated_by`.
5. Apply decisions locally with:
   ```bash
   devenv shell -- apply-decisions <mailbox> <exported.csv>
   ```
   This quarantines files marked `delete`; it does NOT permanently delete them.

### FTP Deploy Steps

These steps upload only the webroot to a shared hosting server.

1. **Prepare** `web/config/local.php` on your machine (see Config Instructions below).
2. **Upload `web/public/`** contents to the server's webroot (e.g. `public_html/` or `httpdocs/`):
   ```bash
   # Using lftp:
   lftp sftp://<user>@<host>
   mirror -R web/public/ public_html/
   ```
   Or drag-and-drop via a GUI FTP client (FileZilla etc.).
3. **Upload `web/config/local.php`** to `<webroot>/../config/local.php` (one level above webroot, outside the served directory).
4. **Upload `web/src/`** to `<webroot>/../src/` (not inside webroot).
5. **Run migrations** once on the server database:
   ```bash
   mysql -u <user> -p <dbname> < web/migrations/001_schema.sql
   # repeat for each migration file in order
   ```
6. **Verify** by visiting `https://<host>/` — you should see the login page.
7. **Secure the config**: confirm `web/config/` and `web/src/` are NOT accessible from the browser (Apache blocks them via `.htaccess`; verify with `curl -I https://<host>/config/` — expect 403).

### Config Instructions

1. Copy the example:
   ```bash
   cp web/config/local.php.example web/config/local.php
   ```
2. Edit `web/config/local.php`:
   - `data_dir`: absolute path to the data directory on the server.
   - `db.host`, `db.dbname`, `db.user`, `db.password`: your MySQL credentials.
   - `coworker_password_hash`: generate with:
     ```bash
     devenv shell -- php -r "echo password_hash('yourpassword', PASSWORD_BCRYPT);"
     ```
   - `admin_password_hash`: same as above, different password.
   - `session.name`: change if hosting multiple apps on the same domain.
3. **Never commit `local.php`** — it's in `.gitignore`.

### Maintenance Mode

To take the app offline (e.g. during DB migration):
```bash
# Enable maintenance (HTTP 503):
touch web/maintenance.flag

# Disable maintenance:
rm web/maintenance.flag
```
The app returns HTTP 503 with `Retry-After: 3600` while the flag exists.


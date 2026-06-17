# MrijaArchive

MrijaArchive is a local mailbox archive viewer for mrija.org. The repository has
two active parts:

- `maildir_report`: Python tools that parse Maildir data, extract attachments,
  and build SQLite archive indexes.
- `mrija_client`: a local FastAPI + HTMX web UI that searches and browses the
  SQLite archive. The Windows desktop build wraps this local server with
  pywebview/PyInstaller.

The current runtime is Python + SQLite. Docker, PHP, MySQL, and the old `web/`
application are not part of the checked-in client runtime.

## Repository Layout

```text
src/maildir_report/       Maildir parsing, attachment extraction, indexing
src/mrija_client/         FastAPI app, SQLite query layer, HTMX templates
launcher/windows/         pywebview/PyInstaller launcher and package scripts
dev/windows/              Windows development helper scripts
dev/virt-manager/         Linux-to-Windows-VM source copy helper
tests/                    pytest suite
data/                     local/generated archive data, not committed
reports/                  generated reports, not committed
logs/                     generated logs, not committed
```

## Development Setup

Use the project `devenv` shell when available:

```bash
devenv shell
```

Or install the Python dependencies from `pyproject.toml` plus the Windows build
requirements when building the executable.

Run tests from the repository root:

```bash
python -B -m pytest tests -q
```

Run the source client against a compatible SQLite archive:

```bash
python -B -m mrija_client --db data/index/mail_index.sqlite --no-tui
```

Then open the printed local URL, normally `http://127.0.0.1:8080`.

## Archive Data Flow

```text
remote Maildir / local fixture
      |
      v
data/mailboxes/<mailbox>/maildir/.maildir/
      |
      v
src/maildir_report.extract_attachments
      |
      v
data/mailboxes/<mailbox>/attachments/
      |
      v
src/maildir_report.index_mailbox
      |
      v
data/mailboxes/<mailbox>/index.sqlite
data/index/mail_index.sqlite
      |
      v
mrija_client FastAPI/HTMX viewer
```

The SQLite schema used by the client contains `archive_emails` and
`archive_attachments`. The UI reads from SQLite directly; filters and pagination
are applied in SQL.

## Windows Development

First-time setup in PowerShell 7:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\dev\windows\setup-dev.ps1
```

Daily workflow:

```powershell
.\dev\windows\load-env.ps1
.\dev\windows\build-client-db.ps1
.\dev\windows\run-client.ps1
```

Run tests:

```powershell
.\dev\windows\test.ps1
```

## Windows Package

Build the executable on Windows:

```bat
cd launcher\windows
build.bat
package.bat
```

`package.bat` expects `dist\MrijaArchive.exe` and
`data\index\mail_index.sqlite`, then creates `MrijaArchive-v1.zip`.

## Publishing Data Updates

The client can download a gzipped SQLite archive from the configured update
server. The update manifest is read by `src/mrija_client/updater.py` and must
include the database filename, URL, version, and SHA-256.

The helper scripts under `scripts/` are admin tooling for publishing snapshots.
Review them before use because they are environment-specific.

## Notes

- Do not commit `data/`, `logs/`, `reports/`, package ZIPs, or local `.env`
  files.
- Real server sync requires SSH key access to the hosting account; password
  automation is intentionally unsupported.
- `devenv.nix` still contains some legacy helper scripts. Prefer the Python
  client commands above unless a legacy command has been verified for the
  current checkout.

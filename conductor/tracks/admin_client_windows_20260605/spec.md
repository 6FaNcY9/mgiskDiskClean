# Spec: Linux Admin Pipeline and Windows Client App

## Goal

Split the project into two clear operating modes:

1. **Admin/dev version, Linux only**
   - Runs sync/import/index/migration/release tooling.
   - Owns real mailbox/archive data ingestion.
   - Builds and publishes client-ready database updates.
   - Can push updates to a DigitalOcean droplet.

2. **Client version, Windows only**
   - User runs one `MrijaArchive.exe`.
   - No terminal.
   - Client can choose a local database/update file from Windows Explorer.
   - If internet is available, client can download the newest archive update from the DigitalOcean droplet.
   - Client uses locally stored data after download/import.

## Current Useful Assets

- `launcher/windows/app.py`: existing PyInstaller/pywebview launcher.
- `launcher/windows/app.spec`, `build.bat`, `package.bat`: current Windows build/package path.
- `.github/workflows/build-windows-exe.yml`: Windows artifact build.
- `scripts/droplet-cloud-init.yaml`: DigitalOcean bootstrap for static updates and activity logging.
- `scripts/push-update.sh`: Linux admin script that uploads SQL dump plus manifest to droplet.
- `web/public/api/check-update.php` and `apply-update.php`: update-check/apply endpoints for web UI.
- `web/public/api/log-event.php`: optional activity logging to droplet.
- `web/src/cli/migrate.php`, `import_archive.php`, `search_archive.php`: admin/runtime database CLIs.
- `docker/qa-archive-docker.sh`, `web/scripts/qa-archive.sh`: QA helpers.

## Product Boundary

### Linux Admin

- May require Docker, shell, SSH keys, MariaDB tools.
- May access sensitive server/mailbox credentials.
- Produces client-safe artifacts.
- Owns DigitalOcean deploy/update publishing.

### Windows Client

- No Linux tooling.
- No real server/mailbox credentials.
- No source checkout required.
- No terminal required.
- Must work offline with a selected local DB/update file.
- Must optionally fetch latest update from DigitalOcean when online.

## Desired Client Data Model

The client should support:

- **Local selection**: choose an archive DB/update file through Explorer.
- **Remembered local store**: copy/import selected data into `%APPDATA%\MrijaArchive\`.
- **Remote update**: fetch droplet `manifest.json`, download artifact, verify SHA-256, apply/import.
- **Offline use**: last successfully imported archive remains usable.

Final decision needed during implementation:

- Keep Docker/MariaDB for Windows client, importing `.sql.gz` dumps from droplet, or
- Move Windows client to a lighter local SQLite runtime.

Recommendation: first stabilize the existing Docker/MariaDB client path, because scripts already publish SQL dumps and launcher already manages Docker. Then evaluate SQLite client simplification as a later optimization.

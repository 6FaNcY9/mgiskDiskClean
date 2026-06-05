# Plan: Linux Admin Pipeline and Windows Client App

## Phase 0: Inventory and Cleanup

- [ ] **Task: Preserve completed Gemini UI work**
  - [ ] Commit or checkpoint the completed `feature/ui-redesign` work before larger changes.
  - [ ] Confirm `conductor/tracks/ui_redesign_20260604/plan.md` has no unchecked tasks.
  - [ ] Confirm full test suite passes after checkpoint.

- [ ] **Task: Inventory scripts and runtime paths**
  - [ ] Document Linux admin scripts: `scripts/push-update.sh`, `scripts/droplet-cloud-init.yaml`, `web/scripts/qa-archive.sh`, `docker/qa-archive-docker.sh`.
  - [ ] Document Windows client scripts: `launcher/windows/app.py`, `build.bat`, `package.bat`, `package-data-update.sh`, GitHub Actions workflow.
  - [ ] Mark stale docs that still describe older boss/client flow or outdated UI.

## Phase 1: Linux Admin Version

- [ ] **Task: Define admin-only workflow**
  - [ ] Document exact Linux commands for migrate, import, QA, export/update.
  - [ ] Keep mailbox sync/import credentials out of client artifacts.
  - [ ] Add a single admin README section for "produce client update".

- [ ] **Task: Harden admin update publishing**
  - [ ] Review `scripts/push-update.sh` for `.env.push`, SSH-key, manifest, checksum, and retention behavior.
  - [ ] Add dry-run/check mode if missing.
  - [ ] Add verification command that fetches the droplet manifest and validates fields.
  - [ ] Ensure no password-based SSH path exists.

- [ ] **Task: DigitalOcean droplet update service**
  - [ ] Review `scripts/droplet-cloud-init.yaml`.
  - [ ] Confirm it serves `/updates/manifest.json` and update artifacts.
  - [ ] Confirm `/log` is bearer-token gated.
  - [ ] Add documentation for first boot, token retrieval, TLS/certbot, and rotation.

## Phase 2: Windows Client Data Flow

- [ ] **Task: Choose and document client archive artifact format**
  - [ ] Decide v1 artifact: SQL dump (`mrija-*.sql.gz`) or SQLite (`mail_index.sqlite`).
  - [ ] Match format with current `push-update.sh` and `apply-update.php`.
  - [ ] Document import/apply behavior and rollback behavior.

- [ ] **Task: Local file picker in Windows client**
  - [ ] Add launcher UI action to choose archive DB/update file through Explorer.
  - [ ] Copy chosen file into `%APPDATA%\MrijaArchive\data\updates\`.
  - [ ] Verify checksum when manifest exists, or compute local SHA-256 for audit log.
  - [ ] Trigger import/apply without terminal.
  - [ ] Add launcher tests with mocked file dialog and subprocess calls.

- [ ] **Task: Remote DigitalOcean update in Windows client**
  - [ ] Read update server URL from bundled/default config or user settings.
  - [ ] Fetch `/updates/manifest.json`.
  - [ ] Download artifact with progress.
  - [ ] Verify SHA-256 before applying.
  - [ ] Store last applied version locally.
  - [ ] Handle offline/no-update/error states clearly.

## Phase 3: Windows Client App Experience

- [ ] **Task: Launcher state model**
  - [ ] Define states: first run, no data, data selected, update available, updating, running, stopped, error.
  - [ ] Surface state in pywebview/tk UI with no terminal.
  - [ ] Keep all subprocess calls hidden on Windows.

- [ ] **Task: Client runtime hardening**
  - [ ] Bind web UI to localhost only.
  - [ ] Ensure client has no admin sync scripts exposed in UI.
  - [ ] Ensure update apply validates artifact path and checksum.
  - [ ] Ensure infected attachments remain blocked unless explicit bypass is unavailable to normal client UI.

- [ ] **Task: Windows build and package**
  - [ ] Verify `app_bundle.zip` creation path for PyInstaller.
  - [ ] Verify `.github/workflows/build-windows-exe.yml` triggers on right branch.
  - [ ] Verify `package.bat` output includes `MrijaArchive.exe`, README, and optional starter data.
  - [ ] Add boss/client README with only double-click/use/update instructions.

## Phase 4: Project Improvements

- [ ] **Task: Documentation cleanup**
  - [ ] Separate Linux admin docs from Windows client docs.
  - [ ] Update README sections that still describe old shared-hosting/coworker flow as main path.
  - [ ] Keep historical docs under `docs/` but mark superseded where needed.

- [ ] **Task: Test coverage**
  - [ ] Expand launcher tests for data selection, remote update, checksum, state transitions.
  - [ ] Add API tests for update manifest/apply paths if not covered.
  - [ ] Keep full suite green.

- [ ] **Task: Release checklist**
  - [ ] Linux admin can publish update to droplet.
  - [ ] Windows exe builds on GitHub Actions.
  - [ ] Windows client starts with no terminal.
  - [ ] Client can choose local archive through Explorer.
  - [ ] Client can fetch/apply droplet update when online.
  - [ ] Client can run offline using last local archive.
  - [ ] Full tests pass.

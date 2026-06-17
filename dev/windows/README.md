# Windows Dev Tools

This folder is for developing and testing the Docker-free Windows client on the
`feature/docker-free-windows-client` branch.

The goal is a repeatable Windows setup that does not require Docker Desktop,
WSL2, Hyper-V, or BIOS virtualization. The client runtime is:

- Python launcher during development
- FastAPI local web server bound to `127.0.0.1`
- SQLite database at `data/client/mail_archive.sqlite`

## First-Time Setup

Open PowerShell 7 as Administrator:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\dev\windows\setup-dev.ps1
```

Close and reopen PowerShell after tool installation so PATH changes are loaded.

## Daily Workflow

Open PowerShell 7 in the repository root:

```powershell
.\dev\windows\load-env.ps1
.\dev\windows\build-client-db.ps1
.\dev\windows\run-client.ps1
```

Then open:

```text
http://127.0.0.1:8080
```

## Test

```powershell
.\dev\windows\test.ps1
```

## Files

- `setup-dev.ps1`: installs Windows developer tools with WinGet.
- `load-env.ps1`: loads the local SQLite/Python environment into the current shell.
- `build-client-db.ps1`: validates and copies `data/index/mail_index.sqlite` to the client DB.
- `run-client.ps1`: starts the local Python web server.
- `test.ps1`: runs the Python test suite.

## Notes

The script `load-env.ps1` is intentionally dot-sourced by itself when run, so it
can set variables in the current terminal. Re-run it after opening a new shell.

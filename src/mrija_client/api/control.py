from __future__ import annotations
import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from mrija_client.state import ClientState

router = APIRouter()

_rate_lock = threading.Lock()
_last_call: dict[str, float] = {}
_RATE_LIMIT_SEC = 10  # destructive endpoints: one call per 10 s


def _audit_request(request: Request) -> dict[str, str]:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",", 1)[0].strip() if forwarded else getattr(request.client, "host", "?")
    return {
        "ip": ip,
        "ua": request.headers.get("user-agent", ""),
        "session": request.cookies.get("mrija_sid", ""),
    }


async def _check_key(x_api_key: str = Header(default="")) -> None:
    expected = os.environ.get("MRIJA_API_KEY", "dev-key")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _rate_limit(endpoint: str) -> None:
    with _rate_lock:
        last = _last_call.get(endpoint, 0.0)
        now = time.monotonic()
        if now - last < _RATE_LIMIT_SEC:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited: wait {_RATE_LIMIT_SEC - int(now - last)}s",
            )
        _last_call[endpoint] = now


class OpenRequest(BaseModel):
    path: str


@router.get("/status", dependencies=[Depends(_check_key)])
async def status():
    from mrija_client.server import get_state
    state = get_state()
    stats = state.db.stats() if state.db else {"email_count": 0, "attachment_count": 0, "last_updated": ""}
    return {
        "state": state.state.value,
        "email_count": stats["email_count"],
        "attachment_count": stats["attachment_count"],
        "last_updated": stats["last_updated"],
        "db_path": str(state.db_path) if state.db_path else None,
        "version": state.version,
    }


@router.post("/open", dependencies=[Depends(_check_key)])
async def open_file(req: OpenRequest):
    from mrija_client.server import get_state
    from mrija_client.db import MailDB
    state = get_state()
    path = Path(req.path).resolve()
    if path.suffix.lower() not in {".sqlite", ".db"}:
        raise HTTPException(400, "Only .sqlite / .db files may be opened")
    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")
    if state.db:
        state.db.close()
    state.db = MailDB(path)
    state.db_path = path
    state.state = ClientState.RUNNING
    state.log_audit("db_opened", f"Database opened: {path.name}", path=str(path))
    return {"state": state.state.value, "db_path": str(path)}


@router.post("/restart", dependencies=[Depends(_check_key)])
async def restart(request: Request):
    _rate_limit("restart")
    from mrija_client.server import get_state
    from mrija_client.db import MailDB
    state = get_state()
    state.log_audit("restart_requested", "Database restart requested", **_audit_request(request))
    if state.db:
        state.db.close()
        state.db = None
    if state.db_path and state.db_path.exists():
        state.db = MailDB(state.db_path)
        state.state = ClientState.RUNNING
    else:
        state.state = ClientState.NO_DATA
    state.log_audit("restart_complete", f"Database restart complete: {state.state.value}", **_audit_request(request))
    return {"state": state.state.value}


@router.post("/shutdown", dependencies=[Depends(_check_key)])
async def shutdown(request: Request):
    _rate_limit("shutdown")
    from mrija_client.server import get_state
    state = get_state()
    state.state = ClientState.STOPPED
    state.log_audit("shutdown_requested", "Server shutdown requested", **_audit_request(request))

    def _stop() -> None:
        import time
        import os as _os
        import signal
        time.sleep(0.2)
        _os.kill(_os.getpid(), signal.SIGTERM)

    threading.Thread(target=_stop, daemon=True).start()
    return {"state": "stopped"}


def _run_sync_impl(state) -> None:
    import subprocess
    from pathlib import Path
    from mrija_client.db import MailDB
    from mrija_client.state import ClientState as _CS

    remote = os.environ.get("MRIJA_SYNC_REMOTE", "")
    remote_path = os.environ.get("MRIJA_SYNC_REMOTE_PATH", "Maildir/")
    maildir = os.environ.get("MRIJA_MAILDIR", "/maildir")
    ssh_key = os.environ.get("MRIJA_SSH_KEY", "/run/secrets/thehost_sshkey")
    db_path = Path(os.environ.get("MRIJA_DB", "/data/mail_index.sqlite"))

    if not remote:
        state.log("Sync: MRIJA_SYNC_REMOTE not configured")
        state.log_audit("sync_skipped", "Sync skipped: MRIJA_SYNC_REMOTE not configured")
        return

    if state.state == _CS.UPDATING:
        state.log("Sync: already in progress, skipping")
        state.log_audit("sync_skipped", "Sync skipped: already in progress")
        return

    state.state = _CS.UPDATING
    state.update_progress = 5
    state.update_status = f"Syncing maildir from {remote}…"
    state.log(f"Sync: rsync {remote}:{remote_path} → {maildir}")

    try:
        r = subprocess.run(
            ["rsync", "-az", "--delete",
             "-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes",
             f"{remote}:{remote_path}", f"{maildir}/"],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"rsync: {r.stderr.strip()[:300]}")

        state.update_progress = 55
        state.update_status = "Reindexing maildir…"
        state.log("Sync: rsync OK, reindexing…")

        from maildir_report.index_mailbox import _init_db, _upsert_email, _upsert_attachment
        from maildir_report.extract_attachments import _is_extractable_part
        from maildir_report.parser import scan_maildir

        global_conn = _init_db(db_path)
        try:
            for user_dir in sorted(Path(maildir).iterdir()):
                if not user_dir.is_dir() or user_dir.name.startswith("."):
                    continue
                user_maildir = user_dir / ".maildir"
                if not user_maildir.exists():
                    continue
                mailbox = user_dir.name
                state.log(f"Sync: indexing {mailbox}…")
                for email_rec in scan_maildir(str(user_maildir)):
                    _upsert_email(global_conn, mailbox, email_rec)
                    for part in email_rec.get("parts", []):
                        if _is_extractable_part(part):
                            _upsert_attachment(global_conn, mailbox, part, email_rec["stable_id"])
            global_conn.commit()
            global_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            global_conn.close()

        state.update_progress = 92
        state.update_status = "Reloading database…"
        if state.db:
            state.db.close()
        state.db = MailDB(db_path)
        state.db_path = db_path
        state.state = _CS.RUNNING
        stats = state.db.stats()
        msg = f"Sync complete — {stats['email_count']:,} emails"
        state.update_status = msg
        state.update_progress = 100
        state.last_sync_at = time.strftime("%Y-%m-%d %H:%M")
        state.last_sync_ok = True
        state.log(msg)
        state.log_audit("sync_complete", msg, email_count=stats["email_count"])
    except Exception as exc:
        state.state = _CS.RUNNING if state.db else _CS.ERROR
        state.update_status = f"Sync failed: {exc}"
        state.last_sync_at = time.strftime("%Y-%m-%d %H:%M")
        state.last_sync_ok = False
        state.log(f"Sync failed: {exc}")
        state.log_audit("sync_failed", f"Sync failed: {exc}")


@router.post("/sync", dependencies=[Depends(_check_key)])
async def trigger_sync(request: Request):
    _rate_limit("sync")
    from mrija_client.server import get_state
    state = get_state()
    if state.state == ClientState.UPDATING:
        state.log_audit("sync_rejected", "Sync rejected because another update is running", **_audit_request(request))
        raise HTTPException(409, "Sync already in progress")
    state.log_audit("sync_requested", "Manual sync requested", **_audit_request(request))
    threading.Thread(target=_run_sync_impl, args=(state,), daemon=True).start()
    return {"status": "started"}


@router.post("/update", dependencies=[Depends(_check_key)])
async def trigger_update(request: Request):
    _rate_limit("update")
    from mrija_client.server import get_state
    from mrija_client.updater import run_update
    state = get_state()
    if state.state == ClientState.UPDATING:
        state.log_audit("update_rejected", "Update rejected because another update is running", **_audit_request(request))
        raise HTTPException(409, "Update already in progress")
    dest_dir = state.db_path.parent if state.db_path else Path(tempfile.mkdtemp())
    state.log_audit("update_requested", "Database update requested", **_audit_request(request))
    threading.Thread(target=run_update, args=(state, dest_dir), daemon=True).start()
    return {"status": "started"}


@router.get("/update/progress", dependencies=[Depends(_check_key)])
async def update_progress():
    from mrija_client.server import get_state
    state = get_state()

    async def _generate():
        while state.state == ClientState.UPDATING:
            payload = json.dumps({
                "percent": state.update_progress,
                "status": state.update_status,
            })
            yield f"data: {payload}\n\n"
            await asyncio.sleep(0.5)
        payload = json.dumps({"percent": 100, "status": state.update_status})
        yield f"data: {payload}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")

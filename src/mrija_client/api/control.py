from __future__ import annotations
import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from mrija_client.state import ClientState

router = APIRouter()

_rate_lock = threading.Lock()
_last_call: dict[str, float] = {}
_RATE_LIMIT_SEC = 10  # destructive endpoints: one call per 10 s


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
    return {"state": state.state.value, "db_path": str(path)}


@router.post("/restart", dependencies=[Depends(_check_key)])
async def restart():
    _rate_limit("restart")
    from mrija_client.server import get_state
    from mrija_client.db import MailDB
    state = get_state()
    if state.db:
        state.db.close()
        state.db = None
    if state.db_path and state.db_path.exists():
        state.db = MailDB(state.db_path)
        state.state = ClientState.RUNNING
    else:
        state.state = ClientState.NO_DATA
    return {"state": state.state.value}


@router.post("/shutdown", dependencies=[Depends(_check_key)])
async def shutdown():
    _rate_limit("shutdown")
    from mrija_client.server import get_state
    state = get_state()
    state.state = ClientState.STOPPED

    def _stop() -> None:
        import time
        import os as _os
        import signal
        time.sleep(0.2)
        _os.kill(_os.getpid(), signal.SIGTERM)

    threading.Thread(target=_stop, daemon=True).start()
    return {"state": "stopped"}


@router.post("/update", dependencies=[Depends(_check_key)])
async def trigger_update():
    _rate_limit("update")
    from mrija_client.server import get_state
    from mrija_client.updater import run_update
    state = get_state()
    if state.state == ClientState.UPDATING:
        raise HTTPException(409, "Update already in progress")
    dest_dir = state.db_path.parent if state.db_path else Path(tempfile.mkdtemp())
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

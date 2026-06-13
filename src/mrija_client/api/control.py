from __future__ import annotations
import os
import threading
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from mrija_client.state import ClientState

router = APIRouter()


def _check_key(x_api_key: str = Header(default="")) -> None:
    expected = os.environ.get("MRIJA_API_KEY", "dev-key")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


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
    path = Path(req.path)
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

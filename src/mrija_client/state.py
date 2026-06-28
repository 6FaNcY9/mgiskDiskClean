from __future__ import annotations
import queue
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mrija_client.db import MailDB


class ClientState(str, Enum):
    NO_DATA  = "no_data"
    STARTING = "starting"
    RUNNING  = "running"
    UPDATING = "updating"
    ERROR    = "error"
    STOPPED  = "stopped"


@dataclass
class AppState:
    state: ClientState = ClientState.NO_DATA
    db: "MailDB | None" = None
    db_path: Path | None = None
    update_progress: int = 0
    update_status: str = ""
    error_message: str = ""
    version: str = ""
    manifest_version: str = ""
    mode: str = "user"
    last_sync_at: str = ""
    last_sync_ok: bool | None = None
    log_queue: queue.SimpleQueue = field(default_factory=queue.SimpleQueue)
    logs: list[str] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    audit_events: list[dict] = field(default_factory=list)
    sessions: dict[str, dict] = field(default_factory=dict)

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"{ts}  {msg}"
        self.log_queue.put(entry)
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs.pop(0)

    def log_request(self, ip: str, method: str, path: str,
                    status: int, ms: int, ua: str) -> None:
        self.requests.append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "at": datetime.now().isoformat(timespec="seconds"),
            "ip": ip,
            "method": method,
            "path": path,
            "status": status,
            "ms": ms,
            "client": _detect_client(ua),
            "user_agent": ua[:240],
        })
        if len(self.requests) > 500:
            self.requests.pop(0)

    def log_audit(self, event: str, message: str, *, ip: str = "",
                  ua: str = "", session: str = "", **details: object) -> None:
        now = datetime.now()
        entry = {
            "ts": now.strftime("%H:%M:%S"),
            "at": now.isoformat(timespec="seconds"),
            "event": event,
            "message": message,
            "ip": ip,
            "client": _detect_client(ua),
            "user_agent": ua[:240],
            "session": session[:12],
            "details": details,
        }
        self.audit_events.append(entry)
        if len(self.audit_events) > 1000:
            self.audit_events.pop(0)
        self._append_audit_file(entry)

    def start_session(self, sid: str, *, ip: str = "", ua: str = "") -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.sessions[sid] = {
            "id": sid[:12],
            "created_at": now,
            "last_seen_at": now,
            "ip": ip,
            "client": _detect_client(ua),
            "user_agent": ua[:240],
            "requests": 0,
        }

    def touch_session(self, sid: str, *, ip: str = "", ua: str = "") -> None:
        session = self.sessions.get(sid)
        if not session:
            return
        session["last_seen_at"] = datetime.now().isoformat(timespec="seconds")
        session["ip"] = ip or session["ip"]
        session["client"] = _detect_client(ua)
        session["user_agent"] = ua[:240]
        session["requests"] = int(session.get("requests", 0)) + 1

    def end_session(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        return self.sessions.pop(sid, None)

    def _append_audit_file(self, entry: dict) -> None:
        audit_path = os.environ.get("MRIJA_AUDIT_LOG", "")
        if not audit_path:
            return
        try:
            path = Path(audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            self.log(f"Audit file write failed: {exc}")


def _detect_client(ua: str) -> str:
    ua = ua.lower()
    if "pywebview" in ua:
        return "windows-app"
    if "python-urllib" in ua or "python/" in ua:
        return "updater"
    if "mozilla" in ua or "chrome" in ua or "safari" in ua:
        return "browser"
    return "other"

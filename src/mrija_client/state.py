from __future__ import annotations
import queue
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
    log_queue: queue.SimpleQueue = field(default_factory=queue.SimpleQueue)
    logs: list[str] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)

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
            "ip": ip,
            "method": method,
            "path": path,
            "status": status,
            "ms": ms,
            "client": _detect_client(ua),
        })
        if len(self.requests) > 500:
            self.requests.pop(0)


def _detect_client(ua: str) -> str:
    ua = ua.lower()
    if "pywebview" in ua:
        return "windows-app"
    if "python-urllib" in ua or "python/" in ua:
        return "updater"
    if "mozilla" in ua or "chrome" in ua or "safari" in ua:
        return "browser"
    return "other"

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
    logs: list = field(default_factory=list)

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"{ts}  {msg}"
        self.log_queue.put(entry)
        self.logs.append(entry)
        if len(self.logs) > 100:
            self.logs.pop(0)

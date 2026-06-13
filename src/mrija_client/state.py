from __future__ import annotations
from dataclasses import dataclass
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

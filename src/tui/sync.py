"""
sync.py — subprocess runner for the mail archive TUI.

Runs devenv sync-all commands in a background thread and streams
stdout/stderr line-by-line to a callback (used to feed Textual's Log widget).
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable


# ---------------------------------------------------------------------------
# Sync log entry (for persistent history)
# ---------------------------------------------------------------------------


@dataclass
class SyncLogEntry:
    line: str
    ts: datetime = field(default_factory=datetime.now)
    is_error: bool = False

    def formatted(self) -> str:
        return f"[{self.ts.strftime('%H:%M:%S')}] {self.line}"


# ---------------------------------------------------------------------------
# Sync runner
# ---------------------------------------------------------------------------


class SyncRunner:
    """Runs sync-all in a thread, streams output via callback."""

    MAX_LOG = 2000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self.log: deque[SyncLogEntry] = deque(maxlen=self.MAX_LOG)
        self._devenv_root = os.environ.get("DEVENV_ROOT", ".")

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def sync_mailbox(
        self,
        mailbox: str,
        on_line: Callable[[str, bool], None] | None = None,
        on_done: Callable[[int], None] | None = None,
    ) -> bool:
        """Start sync for one mailbox. Returns False if already running."""
        return self._start(["sync-all", "--mailbox", mailbox], on_line, on_done)

    def sync_all(
        self,
        on_line: Callable[[str, bool], None] | None = None,
        on_done: Callable[[int], None] | None = None,
    ) -> bool:
        """Start sync for all mailboxes. Returns False if already running."""
        return self._start(["sync-all"], on_line, on_done)

    def _start(
        self,
        cmd: list[str],
        on_line: Callable[[str, bool], None] | None,
        on_done: Callable[[int], None] | None,
    ) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True

        self._thread = threading.Thread(
            target=self._run,
            args=(cmd, on_line, on_done),
            daemon=True,
        )
        self._thread.start()
        return True

    def _run(
        self,
        cmd: list[str],
        on_line: Callable[[str, bool], None] | None,
        on_done: Callable[[int], None] | None,
    ) -> None:
        env = os.environ.copy()
        # Resolve devenv command path — devenv puts scripts on PATH in its shell
        full_cmd = cmd  # works when running inside devenv shell

        header = f"==> Starting: {' '.join(cmd)}"
        self._emit(header, False, on_line)

        try:
            proc = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                cwd=self._devenv_root,
            )
            for raw_line in iter(proc.stdout.readline, ""):
                line = raw_line.rstrip()
                if not line:
                    continue
                is_err = "ERROR" in line or "error" in line.lower()
                self._emit(line, is_err, on_line)
            proc.wait()
            rc = proc.returncode
        except FileNotFoundError:
            self._emit(
                f"ERROR: command not found: {cmd[0]!r} — are you inside devenv shell?",
                True,
                on_line,
            )
            rc = 127
        except Exception as exc:
            self._emit(f"ERROR: {exc}", True, on_line)
            rc = 1
        finally:
            with self._lock:
                self._running = False

        footer = f"==> Done (exit {rc})" if rc == 0 else f"==> FAILED (exit {rc})"
        self._emit(footer, rc != 0, on_line)

        if on_done:
            on_done(rc)

    def _emit(
        self,
        line: str,
        is_err: bool,
        callback: Callable[[str, bool], None] | None,
    ) -> None:
        entry = SyncLogEntry(line=line, is_error=is_err)
        self.log.append(entry)
        if callback:
            callback(line, is_err)

    def log_lines(self) -> list[str]:
        """Return all sync log lines formatted (newest first)."""
        return [e.formatted() for e in reversed(self.log)]

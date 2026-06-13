from __future__ import annotations
import threading
import time
import webbrowser
from mrija_client.state import AppState, ClientState

import importlib.util
_RICH = importlib.util.find_spec("rich") is not None


def _make_panel(state: AppState, server_url: str):
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold cyan", min_width=14)
    t.add_column()

    color = {"running": "green", "updating": "yellow",
              "error": "red", "no_data": "dim"}.get(state.state.value, "white")
    t.add_row("State", Text(state.state.value, style=color))

    if state.db and state.state != ClientState.UPDATING:
        try:
            stats = state.db.stats()
            t.add_row("Emails", str(stats["email_count"]))
            t.add_row("Attachments", str(stats["attachment_count"]))
            t.add_row("Last updated", stats["last_updated"] or "—")
        except Exception:
            pass

    if state.state == ClientState.UPDATING:
        filled = int(state.update_progress / 5)
        bar = "█" * filled + "░" * (20 - filled)
        t.add_row("Progress", f"[{bar}] {state.update_progress}%")
        t.add_row("Status", state.update_status)

    if state.error_message:
        t.add_row("Error", Text(state.error_message, style="red"))

    t.add_row("Server", server_url)
    t.add_row("Keys", "[q] quit  [u] update  [b] browser")

    return Panel(t, title="[bold]MrijaArchive[/bold]", border_style="dim blue")


def run_tui(state: AppState, server_url: str) -> None:
    if not _RICH:
        print(f"Server running at {server_url} — Ctrl+C to stop")
        try:
            while state.state != ClientState.STOPPED:
                time.sleep(1)
        except KeyboardInterrupt:
            state.state = ClientState.STOPPED
        return

    from rich.console import Console
    from rich.live import Live

    console = Console()
    stop_event = threading.Event()

    def _keys() -> None:
        import tempfile
        from pathlib import Path
        while not stop_event.is_set():
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                state.state = ClientState.STOPPED
                stop_event.set()
                break
            cmd = line.strip().lower()
            if cmd == "q":
                state.state = ClientState.STOPPED
                stop_event.set()
            elif cmd == "u":
                from mrija_client.updater import run_update
                dest = state.db_path.parent if state.db_path else Path(tempfile.mkdtemp())
                threading.Thread(target=run_update, args=(state, dest), daemon=True).start()
            elif cmd == "b":
                webbrowser.open(server_url)

    threading.Thread(target=_keys, daemon=True).start()

    try:
        with Live(console=console, refresh_per_second=2) as live:
            while state.state != ClientState.STOPPED:
                live.update(_make_panel(state, server_url))
                time.sleep(0.5)
    except KeyboardInterrupt:
        state.state = ClientState.STOPPED
    finally:
        stop_event.set()

from __future__ import annotations
import threading
import time
import webbrowser
from mrija_client.state import AppState, ClientState

import importlib.util
_TEXTUAL = importlib.util.find_spec("textual") is not None
_RICH    = importlib.util.find_spec("rich") is not None


# ── shared Rich renderable for status panel ───────────────────────────────────

def _build_status_table(state: AppState, server_url: str):
    from rich.table import Table
    from rich.text import Text

    t = Table.grid(padding=(0, 2))
    t.add_column(style="#9ca3af", min_width=10)
    t.add_column()

    _style = {
        "running":  "bold green",
        "updating": "bold yellow",
        "error":    "bold red",
        "no_data":  "dim",
        "starting": "cyan",
        "stopped":  "dim",
    }.get(state.state.value, "white")
    t.add_row("State", Text(state.state.value.upper(), style=_style))

    if state.db and state.state != ClientState.UPDATING:
        try:
            stats = state.db.stats()
            t.add_row("Emails",  f"{stats['email_count']:,}")
            t.add_row("Attach",  f"{stats['attachment_count']:,}")
            last = stats.get("last_updated") or ""
            t.add_row("Updated", last[:10] if last else "—")
        except Exception:
            pass

    if state.version:
        t.add_row("Version", state.version)
    t.add_row("Server", server_url)

    if state.state == ClientState.UPDATING:
        pct    = state.update_progress
        filled = int(pct / 5)
        bar    = "█" * filled + "░" * (20 - filled)
        t.add_row("", "")
        t.add_row("Progress", f"[yellow]{pct}%[/yellow]")
        t.add_row("", f"[bold yellow][{bar}][/bold yellow]")
        t.add_row("Status", Text(state.update_status, style="yellow"))

    if state.error_message:
        t.add_row("Error", Text(state.error_message, style="red"))

    return t


# ── Textual full-screen TUI ───────────────────────────────────────────────────

def _run_textual(state: AppState, server_url: str) -> None:
    import queue as _queue
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Static, RichLog
    from textual.containers import Horizontal

    _css = """
Screen {
    background: #111827;
    color: #e2e8f0;
}
Header {
    background: #1e1b4b;
    color: #818cf8;
    text-style: bold;
}
Footer {
    background: #1e1b4b;
    color: #6b7280;
}
Horizontal {
    height: 1fr;
}
#status {
    width: 36;
    border: round #4338ca;
    margin: 0 1 0 0;
    padding: 0 1;
    background: #111827;
}
#activity {
    width: 1fr;
    border: round #4338ca;
    background: #111827;
}
"""

    class MrijaApp(App[None]):
        TITLE    = "MrijaArchive"
        CSS      = _css
        BINDINGS = [
            ("q", "quit_app",       "Quit"),
            ("u", "trigger_update", "Update"),
            ("b", "open_browser",   "Browser"),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal():
                yield Static(id="status")
                yield RichLog(id="activity", highlight=True, markup=True,
                              wrap=True, max_lines=1000)
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(0.5, self._poll)

        def _poll(self) -> None:
            self.query_one("#status", Static).update(
                _build_status_table(state, server_url)
            )
            log = self.query_one("#activity", RichLog)
            while True:
                try:
                    log.write(state.log_queue.get_nowait())
                except _queue.Empty:
                    break
            if state.state == ClientState.STOPPED:
                self.exit()

        def action_quit_app(self) -> None:
            state.state = ClientState.STOPPED
            self.exit()

        def action_trigger_update(self) -> None:
            import tempfile
            from pathlib import Path
            from mrija_client.updater import run_update
            if state.state == ClientState.UPDATING:
                return
            dest = state.db_path.parent if state.db_path else Path(tempfile.mkdtemp())
            threading.Thread(target=run_update, args=(state, dest), daemon=True).start()

        def action_open_browser(self) -> None:
            webbrowser.open(server_url)

    MrijaApp().run()


# ── Rich Live fallback (no Textual) ──────────────────────────────────────────

def _make_panel(state: AppState, server_url: str):
    from rich.panel import Panel
    t = _build_status_table(state, server_url)
    return Panel(t, title="[bold]MrijaArchive[/bold]", border_style="dim blue")


# ── entry point ───────────────────────────────────────────────────────────────

def run_tui(state: AppState, server_url: str) -> None:
    if _TEXTUAL:
        _run_textual(state, server_url)
        return

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

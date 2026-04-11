"""
main.py — Mail Archive TUI

Launch with:  python -m tui.main   (or the mail-browser devenv command)

Features:
  - Auto-starts MariaDB on launch (waits for socket)
  - Mailbox browser with stats (email/attachment count, last sync, size)
  - Email list with pagination (100/page)
  - Full-text search (/)
  - Date range filter (D)
  - Sender filter (F)
  - Keyboard-driven mailbox switcher (M — fuzzy)
  - New-since-last-sync highlight (imported after session start)
  - Email detail view (Enter) — body + attachments
  - Open attachment with xdg-open (O)
  - Export email as .eml to ~/Downloads (E)
  - Sync selected mailbox (Ctrl+S)
  - Sync ALL mailboxes (Ctrl+A)
  - Log panel: Sync / DB / App  (F1 / F2 / F3)
  - Connection status bar (footer)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from tui.db import MailDB
from tui.sync import SyncRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_date(s: str | None) -> str:
    if not s:
        return "—"
    return str(s)[:16]  # trim seconds


def start_db_service() -> tuple[bool, str]:
    """Try to start MariaDB if socket isn't ready. Returns (ok, message)."""
    sock = os.environ.get(
        "MYSQL_UNIX_PORT",
        os.path.join(os.environ.get("DEVENV_STATE", ".devenv/state"), "mysql.sock"),
    )
    # Already running?
    if Path(sock).exists():
        return True, f"Socket found: {sock}"

    # Try launching via devenv process manager (non-blocking)
    try:
        subprocess.Popen(
            ["devenv", "up"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass  # Not in devenv shell — socket may still exist

    # Wait up to 15s
    for _ in range(15):
        if Path(sock).exists():
            return True, f"MariaDB started: {sock}"
        time.sleep(1)

    return False, f"MariaDB socket not found after 15s: {sock}"


# ---------------------------------------------------------------------------
# Fuzzy mailbox picker modal
# ---------------------------------------------------------------------------


class MailboxPicker(ModalScreen[str | None]):
    """Press M to open. Type to fuzzy-filter. Enter to select."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
    ]

    CSS = """
    MailboxPicker {
        align: center middle;
    }
    #picker_dialog {
        width: 50;
        height: 24;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #picker_input {
        margin-bottom: 1;
    }
    """

    def __init__(self, mailboxes: list[str]) -> None:
        super().__init__()
        self._all = mailboxes

    def compose(self) -> ComposeResult:
        with Vertical(id="picker_dialog"):
            yield Label("Switch Mailbox (type to filter)")
            yield Input(placeholder="Filter...", id="picker_input")
            yield OptionList(*[Option(m, id=m) for m in self._all], id="picker_list")

    def on_mount(self) -> None:
        self.query_one("#picker_input", Input).focus()

    @on(Input.Changed, "#picker_input")
    def filter_list(self, event: Input.Changed) -> None:
        query = event.value.lower()
        lst = self.query_one("#picker_list", OptionList)
        lst.clear_options()
        filtered = [m for m in self._all if query in m.lower()]
        for m in filtered:
            lst.add_option(Option(m, id=m))

    @on(OptionList.OptionSelected, "#picker_list")
    def selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Input.Submitted, "#picker_input")
    def submit_first(self, event: Input.Submitted) -> None:
        lst = self.query_one("#picker_list", OptionList)
        if lst.option_count > 0:
            self.dismiss(str(lst.get_option_at_index(0).id))
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Date range filter modal
# ---------------------------------------------------------------------------


class DateFilterModal(ModalScreen[tuple[str, str] | None]):
    """Press D to open. Enter date range (YYYY-MM or YYYY)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    CSS = """
    DateFilterModal { align: center middle; }
    #df_dialog {
        width: 44; height: 12;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="df_dialog"):
            yield Label("Date range filter  (YYYY-MM-DD or leave blank)")
            yield Input(placeholder="From e.g. 2023-01-01", id="df_from")
            yield Input(placeholder="To   e.g. 2023-12-31", id="df_to")
            yield Button("Apply", variant="primary", id="df_apply")

    def on_mount(self) -> None:
        self.query_one("#df_from", Input).focus()

    @on(Button.Pressed, "#df_apply")
    def apply(self) -> None:
        frm = self.query_one("#df_from", Input).value.strip()
        to = self.query_one("#df_to", Input).value.strip()
        self.dismiss((frm, to))


# ---------------------------------------------------------------------------
# Sender filter modal
# ---------------------------------------------------------------------------


class SenderFilterModal(ModalScreen[str | None]):
    """Press F to filter by sender."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    CSS = """
    SenderFilterModal { align: center middle; }
    #sf_dialog {
        width: 44; height: 9;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="sf_dialog"):
            yield Label("Filter by sender (partial match)")
            yield Input(placeholder="e.g. gmail.com or alice@", id="sf_input")
            yield Button("Apply", variant="primary", id="sf_apply")

    def on_mount(self) -> None:
        self.query_one("#sf_input", Input).focus()

    @on(Button.Pressed, "#sf_apply")
    def apply(self) -> None:
        val = self.query_one("#sf_input", Input).value.strip()
        self.dismiss(val or None)


# ---------------------------------------------------------------------------
# Email detail modal
# ---------------------------------------------------------------------------


class EmailDetailModal(ModalScreen[None]):
    """Show full email + attachments. Press O to open attachment, E to export."""

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "Close"),
        Binding("e", "export", "Export .eml"),
    ]

    CSS = """
    EmailDetailModal { align: center middle; }
    #detail_dialog {
        width: 90%;
        height: 90%;
        border: thick $primary;
        background: $surface;
        padding: 0;
    }
    #detail_header { height: 6; padding: 1 2; background: $panel; }
    #detail_body { padding: 1 2; }
    #detail_attachments { height: auto; padding: 0 2 1 2; }
    """

    def __init__(self, email: dict, attachments: list[dict], filepath: str) -> None:
        super().__init__()
        self._email = email
        self._attachments = attachments
        self._filepath = filepath  # original .eml path

    def compose(self) -> ComposeResult:
        e = self._email
        with Vertical(id="detail_dialog"):
            # Header block
            with Static(id="detail_header"):
                yield Label(
                    f"[bold]Subject:[/] {e.get('subject', '—')}\n"
                    f"[bold]From:[/]    {e.get('from_addr', '—')}\n"
                    f"[bold]To:[/]      {e.get('to_addrs', '—')}\n"
                    f"[bold]CC:[/]      {e.get('cc_addrs', '') or '—'}\n"
                    f"[bold]Date:[/]    {e.get('date', '—')}  |  "
                    f"Size: {fmt_size(e.get('total_size_bytes'))}  |  "
                    f"Folder: {e.get('folder', '—')}"
                )
            # Body
            with ScrollableContainer(id="detail_body"):
                body = e.get("body_text") or "(no body text)"
                yield Static(body)
            # Attachments
            if self._attachments:
                with Container(id="detail_attachments"):
                    yield Label(f"\n[bold]📎 Attachments ({len(self._attachments)})[/]")
                    for i, att in enumerate(self._attachments):
                        name = att.get("original_filename") or att.get(
                            "stored_path", "?"
                        )
                        size = fmt_size(att.get("size"))
                        mime = att.get("mime", "")
                        yield Button(
                            f"[{i + 1}] {name}  ({size})  {mime}",
                            id=f"att_{i}",
                            variant="default",
                        )
            yield Label(
                "[dim]Esc/Q: close  |  E: export .eml  |  Click attachment to open[/]"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("att_"):
            idx = int(event.button.id.split("_")[1])
            att = self._attachments[idx]
            path = att.get("stored_path", "")
            if path and Path(path).exists():
                try:
                    subprocess.Popen(["xdg-open", path])
                    self.notify(f"Opened: {Path(path).name}")
                except Exception as exc:
                    self.notify(f"Open failed: {exc}", severity="error")
            else:
                self.notify(f"File not found: {path}", severity="warning")

    def action_export(self) -> None:
        """Copy .eml file to ~/Downloads."""
        src = Path(self._filepath)
        if not src.exists():
            self.notify(f"File not found: {src}", severity="error")
            return
        dest_dir = Path.home() / "Downloads"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / src.name
        try:
            shutil.copy2(src, dest)
            self.notify(f"Exported to {dest}")
        except Exception as exc:
            self.notify(f"Export failed: {exc}", severity="error")


# ---------------------------------------------------------------------------
# Main app screen
# ---------------------------------------------------------------------------


class MailArchiveApp(App):
    """Mail Archive TUI — single-screen with switchable log panel."""

    TITLE = "Mail Archive"
    SUB_TITLE = "mailreview"
    FEATURES = frozenset()  # disable mouse tracking — prevents raw escape bytes being typed into Input

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=True),
        Binding("m", "open_mailbox_picker", "Mailbox", show=True),
        Binding("d", "open_date_filter", "Date filter", show=True),
        Binding("f", "open_sender_filter", "Sender filter", show=True),
        Binding("ctrl+s", "sync_mailbox", "Sync mailbox", show=True),
        Binding("ctrl+a", "sync_all", "Sync ALL", show=True),
        Binding("f1", "show_log('sync')", "Sync log", show=True),
        Binding("f2", "show_log('db')", "DB log", show=True),
        Binding("f3", "show_log('app')", "App log", show=True),
        Binding("r", "clear_filters", "Reset filters", show=False),
        Binding("[", "pane_shrink", "◀ Pane", show=False),
        Binding("]", "pane_grow", "Pane ▶", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    CSS = """
    Screen {
        layout: grid;
        grid-size: 1;
        grid-rows: auto 1fr auto;
    }

    #toolbar {
        height: 3;
        layout: horizontal;
        background: $panel;
        padding: 0 1;
    }
    #toolbar Input {
        width: 1fr;
        margin: 0 1;
    }
    #toolbar Label {
        width: auto;
        padding: 1 0;
    }

    #main_area {
        layout: horizontal;
        height: 1fr;
    }

    #left_pane {
        width: 50%;
        border-right: solid $primary-darken-2;
    }

    #mailbox_stats {
        height: 7;
        padding: 1 2;
        background: $panel;
        border-bottom: solid $primary-darken-2;
    }

    #email_table {
        height: 1fr;
    }

    #pagination_bar {
        height: 1;
        background: $panel-darken-1;
        padding: 0 1;
        layout: horizontal;
    }

    #right_pane {
        width: 1fr;
        padding: 1 2;
    }

    #detail_placeholder {
        color: $text-muted;
        padding: 2 2;
    }

    #log_panel {
        height: 10;
        border-top: solid $primary-darken-2;
    }

    #sync_log {
        height: 1fr;
        background: $surface-darken-1;
    }

    #db_log {
        height: 1fr;
        background: $surface-darken-1;
    }

    #app_log {
        height: 1fr;
        background: $surface-darken-1;
    }

    DataTable {
        height: 1fr;
    }

    .new-email {
        color: $success;
    }

    .status-connected {
        color: $success;
    }
    .status-disconnected {
        color: $error;
    }
    """

    # ── reactive state ──────────────────────────────────────────────────────

    current_mailbox: reactive[str] = reactive("", recompose=False)
    search_query: reactive[str] = reactive("", recompose=False)
    sender_filter: reactive[str] = reactive("", recompose=False)
    date_from: reactive[str] = reactive("", recompose=False)
    date_to: reactive[str] = reactive("", recompose=False)
    page: reactive[int] = reactive(0, recompose=False)
    total_emails: reactive[int] = reactive(0, recompose=False)
    left_pane_pct: reactive[int] = reactive(50, recompose=False)

    PAGE_SIZE = 100

    def __init__(self) -> None:
        super().__init__()
        self.db = MailDB()
        self.sync_runner = SyncRunner()
        self._mailboxes: list[str] = []
        self._email_rows: list[dict] = []
        self._new_ids: set[str] = set()
        self._session_start = datetime.now()
        self._selected_email: dict | None = None
        self._app_log_lines: list[str] = []

    # ── compose ─────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()

        # Toolbar: search + active filter labels
        with Horizontal(id="toolbar"):
            yield Label("🔍")
            yield Input(
                placeholder="Full-text search... (press / to focus)", id="search_input"
            )
            yield Label("", id="filter_labels")

        # Main area
        with Horizontal(id="main_area"):
            # Left pane: stats + email list + pagination
            with Vertical(id="left_pane"):
                yield Static("Select a mailbox (press M)", id="mailbox_stats")
                yield DataTable(id="email_table", cursor_type="row", zebra_stripes=True)
                with Horizontal(id="pagination_bar"):
                    yield Label("", id="page_label")
                    yield Button("◀", id="btn_prev", variant="default")
                    yield Button("▶", id="btn_next", variant="default")

            # Right pane: email detail
            with ScrollableContainer(id="right_pane"):
                yield Static("← Select an email to read it", id="detail_placeholder")

        # Log panel (tabbed)
        with TabbedContent(id="log_panel"):
            with TabPane("Sync Log [F1]", id="tab_sync"):
                yield Log(id="sync_log", highlight=True)
            with TabPane("DB Log [F2]", id="tab_db"):
                yield Log(id="db_log", highlight=True)
            with TabPane("App Log [F3]", id="tab_app"):
                yield Log(id="app_log", highlight=True)

        yield Footer()

    # ── startup ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._setup_table()
        self._start_db_worker()

    def _setup_table(self) -> None:
        table = self.query_one("#email_table", DataTable)
        table.add_columns("📅 Date", "👤 From", "📧 Subject", "📎", "Size")

    def _update_subtitle(self, text: str) -> None:
        """Update the app subtitle (called from thread via call_from_thread)."""
        self.sub_title = text

    def watch_left_pane_pct(self, pct: int) -> None:
        """Dynamically resize left pane when left_pane_pct changes."""
        try:
            self.query_one("#left_pane").styles.width = f"{pct}%"
        except Exception:
            pass

    def action_pane_grow(self) -> None:
        """Grow left pane by 5% (] key)."""
        self.left_pane_pct = min(85, self.left_pane_pct + 5)

    def action_pane_shrink(self) -> None:
        """Shrink left pane by 5% ([ key)."""
        self.left_pane_pct = max(15, self.left_pane_pct - 5)

    @work(thread=True)
    def _start_db_worker(self) -> None:
        """Connect to DB (auto-start MariaDB if needed) in background thread."""
        self.call_from_thread(self._app_log, "Checking MariaDB socket...")
        ok, msg = start_db_service()
        self.call_from_thread(self._app_log, msg)

        if not ok:
            self.call_from_thread(
                self._app_log, "ERROR: Could not start MariaDB", error=True
            )
            self.call_from_thread(self._update_subtitle, "\u26a0 DB offline")
            return

        try:
            self.db.connect()
            self.call_from_thread(self._app_log, "Connected to mailreview DB")
            self.call_from_thread(self._update_subtitle, "\u2713 Connected \u2014 mailreview")
            # Load mailboxes from DB in this same thread, then push to UI
            self._load_mailboxes_from_thread()
        except Exception as exc:
            self.call_from_thread(
                self._app_log, f"DB connect failed: {exc}", error=True
            )
            self.call_from_thread(self._update_subtitle, "\u26a0 DB error")

    def _load_mailboxes_from_thread(self) -> None:
        """Load mailboxes and first mailbox emails — runs inside _start_db_worker thread."""
        try:
            rows = self.db.list_mailboxes()
            mailboxes = [r["mailbox"] for r in rows]
            self.call_from_thread(self._app_log, f"Loaded {len(mailboxes)} mailboxes from DB")
            self._mailboxes = mailboxes
            if mailboxes:
                first = mailboxes[0]
                self.call_from_thread(self._app_log, f"Auto-selecting: {first}")
                # Load stats + emails for first mailbox in this thread
                self._load_stats_and_emails_thread(first)
        except Exception as exc:
            self.call_from_thread(self._app_log, f"Failed to load mailboxes: {exc}", error=True)

    def _load_stats_and_emails_thread(self, mailbox: str) -> None:
        """Load stats + emails for mailbox — must run inside a background thread."""
        try:
            # Stats
            stats = self.db.mailbox_stats(mailbox)
            if stats:
                txt = (
                    f"[bold]{mailbox}[/]\n"
                    f"Emails:  {stats['email_count']:,}\n"
                    f"Attachments: {stats['attachment_count'] or 0:,}\n"
                    f"Size:    {fmt_size(stats['total_bytes'])}\n"
                    f"Range:   {fmt_date(str(stats['oldest_date']))} \u2192 {fmt_date(str(stats['newest_date']))}\n"
                    f"Synced:  {fmt_date(str(stats['last_imported']))}"
                )
                self.call_from_thread(
                    self.query_one("#mailbox_stats", Static).update, txt
                )
            # Emails
            count = self.db.count_emails(mailbox)
            email_rows = self.db.list_emails(mailbox, limit=self.PAGE_SIZE, offset=0)
            self._new_ids = self.db.get_recent_stable_ids(mailbox, self._session_start)
            self.current_mailbox = mailbox
            self.call_from_thread(self._populate_table, email_rows, count)
        except Exception as exc:
            self.call_from_thread(self._app_log, f"Load failed: {exc}", error=True)

    def _select_mailbox(self, mailbox: str) -> None:
        self.current_mailbox = mailbox
        self.page = 0
        self._switch_mailbox_worker(mailbox)

    @work(thread=True)
    def _switch_mailbox_worker(self, mailbox: str) -> None:
        """Load stats + emails for newly selected mailbox (runs in its own thread)."""
        self._load_stats_and_emails_thread(mailbox)

    # ── email list ───────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_emails(self) -> None:
        if not self.current_mailbox:
            return
        try:
            count = self.db.count_emails(
                self.current_mailbox,
                search=self.search_query,
                sender_filter=self.sender_filter,
                date_from=self.date_from,
                date_to=self.date_to,
            )
            rows = self.db.list_emails(
                self.current_mailbox,
                search=self.search_query,
                sender_filter=self.sender_filter,
                date_from=self.date_from,
                date_to=self.date_to,
                offset=self.page * self.PAGE_SIZE,
                limit=self.PAGE_SIZE,
            )
            self.call_from_thread(self._populate_table, rows, count)
        except Exception as exc:
            self.call_from_thread(
                self._app_log, f"Load emails failed: {exc}", error=True
            )

    def _populate_table(self, rows: list[dict], total: int) -> None:
        self._email_rows = rows
        self.total_emails = total
        table = self.query_one("#email_table", DataTable)
        table.clear()

        for r in rows:
            is_new = r["stable_id"] in self._new_ids
            att = str(r["attachment_count"] or 0)
            row_vals = (
                fmt_date(r.get("date")),
                r.get("from_addr", "")[:28],
                r.get("subject", "")[:45],
                att if att != "0" else "",
                fmt_size(r.get("total_size_bytes")),
            )
            table.add_row(
                *row_vals,
                key=r["stable_id"],
                label=None,
            )
            if is_new:
                # Style new rows — Textual doesn't have per-row CSS directly,
                # but we mark via rich markup on first cell
                pass  # future: use table.get_row(...) to style

        # Update pagination bar
        pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.query_one("#page_label", Label).update(
            f"Page {self.page + 1}/{pages}  ({total:,} emails)"
        )

        # Update filter labels
        labels = []
        if self.search_query:
            labels.append(f"search: {self.search_query!r}")
        if self.sender_filter:
            labels.append(f"from: {self.sender_filter!r}")
        if self.date_from or self.date_to:
            labels.append(f"date: {self.date_from or '…'}→{self.date_to or '…'}")
        self.query_one("#filter_labels", Label).update(
            "  |  " + "  ·  ".join(labels) if labels else ""
        )

    # ── email detail ─────────────────────────────────────────────────────────

    @on(DataTable.RowSelected, "#email_table")
    def email_selected(self, event: DataTable.RowSelected) -> None:
        stable_id = str(event.row_key.value)
        self._load_email_detail(stable_id)

    @work(thread=True)
    def _load_email_detail(self, stable_id: str) -> None:
        try:
            email = self.db.get_email(self.current_mailbox, stable_id)
            attachments = self.db.get_attachments(self.current_mailbox, stable_id)
            if email:
                self.call_from_thread(self._show_detail, email, attachments)
        except Exception as exc:
            self.call_from_thread(
                self._app_log, f"Load email detail failed: {exc}", error=True
            )

    def _show_detail(self, email: dict, attachments: list[dict]) -> None:
        self._selected_email = email
        right = self.query_one("#right_pane", ScrollableContainer)
        right.remove_children()

        e = email
        header_text = (
            f"[bold]{e.get('subject', '(no subject)')}[/]\n\n"
            f"[dim]From:[/]  {e.get('from_addr', '—')}\n"
            f"[dim]To:[/]    {e.get('to_addrs', '—')}\n"
        )
        if e.get("cc_addrs"):
            header_text += f"[dim]CC:[/]    {e['cc_addrs']}\n"
        header_text += (
            f"[dim]Date:[/]  {e.get('date', '—')}\n"
            f"[dim]Size:[/]  {fmt_size(e.get('total_size_bytes'))}  "
            f"[dim]Folder:[/] {e.get('folder', '—')}\n"
        )

        right.mount(Static(header_text))
        right.mount(Static("─" * 60))

        body = e.get("body_text") or "(no body text)"
        right.mount(Static(body))

        if attachments:
            right.mount(Static(f"\n[bold]📎 Attachments ({len(attachments)})[/]"))
            for i, att in enumerate(attachments):
                name = (
                    att.get("original_filename")
                    or Path(att.get("stored_path", "?")).name
                )
                size = fmt_size(att.get("size"))
                mime = att.get("mime", "")
                right.mount(
                    Button(
                        f"  [{i + 1}] {name}  {size}  [{mime}]",
                        id=f"detail_att_{i}",
                        variant="default",
                    )
                )

        right.mount(Static("\n[dim]E: export .eml  |  Click attachment to open[/]"))
        right.scroll_home()

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("detail_att_"):
            idx = int(bid.split("_")[-1])
            self._open_attachment(idx)
        elif bid == "btn_prev":
            self.action_prev_page()
        elif bid == "btn_next":
            self.action_next_page()

    def _open_attachment(self, idx: int) -> None:
        if not self._selected_email:
            return
        stable_id = self._selected_email.get("stable_id", "")
        atts = self.db.get_attachments(self.current_mailbox, stable_id)
        if idx >= len(atts):
            return
        att = atts[idx]
        path = att.get("stored_path", "")
        if path and Path(path).exists():
            try:
                subprocess.Popen(["xdg-open", path])
                self.notify(f"Opened: {Path(path).name}")
            except Exception as exc:
                self.notify(f"Open failed: {exc}", severity="error")
        else:
            self.notify(f"File not found: {path}", severity="warning")

    # ── actions ──────────────────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        self.query_one("#search_input", Input).focus()

    def action_open_mailbox_picker(self) -> None:
        if not self._mailboxes:
            self.notify("No mailboxes loaded yet")
            return
        self.push_screen(MailboxPicker(self._mailboxes), self._on_mailbox_picked)

    def _on_mailbox_picked(self, result: str | None) -> None:
        if result:
            self._select_mailbox(result)

    def action_open_date_filter(self) -> None:
        self.push_screen(DateFilterModal(), self._on_date_filter)

    def _on_date_filter(self, result: tuple[str, str] | None) -> None:
        if result is not None:
            self.date_from, self.date_to = result
            self.page = 0
            self._load_emails()

    def action_open_sender_filter(self) -> None:
        self.push_screen(SenderFilterModal(), self._on_sender_filter)

    def _on_sender_filter(self, result: str | None) -> None:
        if result is not None:
            self.sender_filter = result
            self.page = 0
            self._load_emails()

    def action_clear_filters(self) -> None:
        self.search_query = ""
        self.sender_filter = ""
        self.date_from = ""
        self.date_to = ""
        self.page = 0
        self.query_one("#search_input", Input).value = ""
        self._load_emails()
        self.notify("Filters cleared")

    def action_prev_page(self) -> None:
        if self.page > 0:
            self.page -= 1
            self._load_emails()

    def action_next_page(self) -> None:
        pages = max(1, (self.total_emails + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self.page < pages - 1:
            self.page += 1
            self._load_emails()

    def action_show_log(self, tab: str) -> None:
        tabs = self.query_one("#log_panel", TabbedContent)
        tab_map = {"sync": "tab_sync", "db": "tab_db", "app": "tab_app"}
        if tab in tab_map:
            tabs.active = tab_map[tab]

    # ── search ───────────────────────────────────────────────────────────────

    @on(Input.Changed, "#search_input")
    def on_search_changed(self, event: Input.Changed) -> None:
        # Only search after 2+ chars (or empty to reset)
        val = event.value.strip()
        if val == self.search_query:
            return
        if len(val) >= 2 or val == "":
            self.search_query = val
            self.page = 0
            self._load_emails()

    @on(Input.Submitted, "#search_input")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        self.search_query = event.value.strip()
        self.page = 0
        self._load_emails()

    # ── sync ────────────────────────────────────────────────────────────────

    def action_sync_mailbox(self) -> None:
        if not self.current_mailbox:
            self.notify("No mailbox selected")
            return
        if self.sync_runner.is_running:
            self.notify("Sync already in progress", severity="warning")
            return
        self.notify(f"Syncing {self.current_mailbox}...")
        self.action_show_log("sync")
        self._run_sync_mailbox(self.current_mailbox)

    def action_sync_all(self) -> None:
        if self.sync_runner.is_running:
            self.notify("Sync already in progress", severity="warning")
            return
        self.notify("Syncing ALL mailboxes...")
        self.action_show_log("sync")
        self._run_sync_all()

    @work(thread=True)
    def _run_sync_mailbox(self, mailbox: str) -> None:
        sync_log = self.query_one("#sync_log", Log)

        def on_line(line: str, is_err: bool) -> None:
            color = "red" if is_err else ""
            text = f"[{color}]{line}[/{color}]" if color else line
            self.call_from_thread(sync_log.write_line, text)

        def on_done(rc: int) -> None:
            if rc == 0:
                self.call_from_thread(self.notify, f"✓ Sync complete: {mailbox}")
                # Refresh stats + email list + new IDs
                # Refresh stats + email list after sync
                self._switch_mailbox_worker(mailbox)
            else:
                self.call_from_thread(
                    self.notify, f"✗ Sync failed (exit {rc})", severity="error"
                )

        self.sync_runner.sync_mailbox(mailbox, on_line=on_line, on_done=on_done)

    @work(thread=True)
    def _run_sync_all(self) -> None:
        sync_log = self.query_one("#sync_log", Log)

        def on_line(line: str, is_err: bool) -> None:
            color = "red" if is_err else ""
            text = f"[{color}]{line}[/{color}]" if color else line
            self.call_from_thread(sync_log.write_line, text)

        def on_done(rc: int) -> None:
            msg = (
                "✓ All mailboxes synced"
                if rc == 0
                else f"✗ Sync ALL failed (exit {rc})"
            )
            sev = "information" if rc == 0 else "error"
            self.call_from_thread(self.notify, msg, severity=sev)
            self.call_from_thread(self._load_mailboxes)

        self.sync_runner.sync_all(on_line=on_line, on_done=on_done)

    # ── export ───────────────────────────────────────────────────────────────

    def action_export(self) -> None:
        """Export selected email as .eml to ~/Downloads."""
        if not self._selected_email:
            self.notify("No email selected")
            return
        filepath = self._selected_email.get("filepath", "")
        if not filepath:
            self.notify("No file path in record", severity="warning")
            return
        src = Path(filepath)
        if not src.exists():
            self.notify(f"File not found: {src}", severity="error")
            return
        dest_dir = Path.home() / "Downloads"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / src.name
        try:
            shutil.copy2(src, dest)
            self.notify(f"Exported: {dest}")
        except Exception as exc:
            self.notify(f"Export failed: {exc}", severity="error")

    # ── DB log refresh ───────────────────────────────────────────────────────

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        if event.tab.id == "tab_db":
            self._refresh_db_log()
        elif event.tab.id == "tab_app":
            self._refresh_app_log()

    def _refresh_db_log(self) -> None:
        log_widget = self.query_one("#db_log", Log)
        log_widget.clear()
        for line in self.db.log_lines():
            log_widget.write_line(line)

    def _refresh_app_log(self) -> None:
        log_widget = self.query_one("#app_log", Log)
        log_widget.clear()
        for line in self._app_log_lines:
            log_widget.write_line(line)

    # ── app log helper ───────────────────────────────────────────────────────

    def _app_log(self, msg: str, error: bool = False) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = "ERROR" if error else "INFO "
        line = f"[{ts}] {prefix}  {msg}"
        self._app_log_lines.append(line)
        # Write to app_log widget if it's visible
        try:
            log_widget = self.query_one("#app_log", Log)
            log_widget.write_line(line)
        except Exception:
            pass

    # ── cleanup ──────────────────────────────────────────────────────────────

    def on_unmount(self) -> None:
        self.db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app = MailArchiveApp()
    app.run()


if __name__ == "__main__":
    main()

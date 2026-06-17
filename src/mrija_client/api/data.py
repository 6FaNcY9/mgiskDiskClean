from __future__ import annotations
import html
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)


def _render(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_env.get_template(name).render(**ctx))


@router.get("/search", response_class=HTMLResponse)
async def search(
    q: str = "",
    mailbox: str = "",
    date_from: str = "",
    date_to: str = "",
    has_attachment: str = "",
    page: int = 0,
):
    q = q[:200]
    page = max(0, page)
    from mrija_client.server import get_state
    state = get_state()
    att_filter: bool | None = (
        True if has_attachment == "true" else
        False if has_attachment == "false" else
        None
    )
    emails = (
        state.db.search(
            q,
            mailbox=mailbox or None,
            date_from=date_from or None,
            date_to=date_to or None,
            has_attachment=att_filter,
            page=page,
        )
        if state.db
        else []
    )
    return _render("search_results.html", emails=emails, q=q, page=page,
                   mailbox=mailbox, date_from=date_from, date_to=date_to,
                   has_attachment=has_attachment)


@router.get("/browse", response_class=HTMLResponse)
async def browse(
    mailbox: str = "",
    date_from: str = "",
    date_to: str = "",
    has_attachment: str = "",
    page: int = 0,
):
    page = max(0, page)
    from mrija_client.server import get_state
    state = get_state()
    att_filter: bool | None = (
        True if has_attachment == "true" else
        False if has_attachment == "false" else
        None
    )
    emails = (
        state.db.browse(
            mailbox=mailbox or None,
            date_from=date_from or None,
            date_to=date_to or None,
            has_attachment=att_filter,
            page=page,
        )
        if state.db
        else []
    )
    return _render("browse.html", emails=emails, mailbox=mailbox, page=page,
                   date_from=date_from, date_to=date_to,
                   has_attachment=has_attachment)


@router.get("/mailboxes", response_class=HTMLResponse)
async def mailboxes_options(selected: str = ""):
    from mrija_client.server import get_state
    state = get_state()
    boxes = state.db.mailboxes() if state.db else []
    if selected and selected not in boxes:
        boxes = [selected] + boxes
    opts = '<option value="">All mailboxes</option>'
    for b in boxes:
        b_esc = html.escape(b)
        sel = ' selected' if b == selected else ''
        opts += f'<option value="{b_esc}"{sel}>{b_esc}</option>'
    return HTMLResponse(opts)


@router.get("/filters", response_class=HTMLResponse)
async def filters_sidebar():
    return _render("filters_sidebar.html", date_from="", date_to="", has_attachment="")


@router.get("/email/{mailbox}/{stable_id}", response_class=HTMLResponse)
async def email_detail(mailbox: str, stable_id: str):
    from mrija_client.server import get_state
    state = get_state()
    if not state.db:
        raise HTTPException(503, "No database loaded")
    email = state.db.get_email(mailbox, stable_id)
    if not email:
        raise HTTPException(404, "Email not found")
    attachments = state.db.get_attachments(mailbox, stable_id)
    return _render("email_detail.html", email=email, attachments=attachments)


@router.get("/status-bar", response_class=HTMLResponse)
async def status_bar():
    from mrija_client.server import get_state
    from mrija_client.state import ClientState
    state = get_state()

    is_updating = state.state == ClientState.UPDATING
    email_count = None
    if state.db and not is_updating:
        try:
            stats = state.db.stats()
            email_count = f"{stats['email_count']:,}"
        except Exception:
            pass

    return _render("status_bar.html",
        state=state.state.value,
        state_label=state.state.value.replace("_", " ").title(),
        email_count=email_count,
        progress=state.update_progress,
        update_status=state.update_status,
        error=state.error_message,
        refresh_interval="1s" if is_updating else "3s",
        is_updating=is_updating,
    )


@router.get("/update-check", response_class=HTMLResponse)
async def update_check():
    from mrija_client.server import get_state
    from mrija_client.updater import fetch_manifest
    state = get_state()
    try:
        manifest = fetch_manifest()
        remote_ver = manifest.get("version", "")
        if remote_ver and remote_ver == state.manifest_version:
            return HTMLResponse(
                '<div id="update-banner" hx-get="/data/update-check"'
                ' hx-trigger="every 300s" hx-swap="outerHTML"></div>'
            )
        size_mb = round(manifest.get("size", 0) / 1024 / 1024, 1)
        return _render("update_banner.html", version=remote_ver, size_mb=size_mb)
    except Exception:
        return HTMLResponse(
            '<div id="update-banner" hx-get="/data/update-check"'
            ' hx-trigger="every 300s" hx-swap="outerHTML"></div>'
        )


@router.get("/logs", response_class=HTMLResponse)
async def logs_fragment():
    from mrija_client.server import get_state
    state = get_state()
    _rich = re.compile(r"\[/?[^\]]*\]")
    lines = [_rich.sub("", ln) for ln in state.logs[-80:]]
    if not lines:
        return HTMLResponse('<div class="log-line dim">No log entries yet.</div>')
    rows = []
    for i, ln in enumerate(lines):
        cls = "log-line fresh" if i >= len(lines) - 3 else "log-line"
        rows.append(f'<div class="{cls}">{html.escape(ln)}</div>')
    return HTMLResponse("".join(rows))


@router.get("/admin-panel", response_class=HTMLResponse)
async def admin_panel_fragment():
    from mrija_client.server import get_state
    state = get_state()
    db_info = str(state.db_path) if state.db_path else "no database"
    return _render("admin_panel.html", db_info=db_info)


@router.get("/attachment/{sha256}")
async def download_attachment(sha256: str):
    from mrija_client.server import get_state
    state = get_state()
    if not state.db:
        raise HTTPException(503, "No database loaded")
    att = state.db.get_attachment_by_sha256(sha256)
    if not att:
        raise HTTPException(404, "Attachment not found")
    if not state.db_path:
        raise HTTPException(503, "No database path")
    data_dir = state.db_path.parent.parent
    file_path = (data_dir / att["stored_path"]).resolve()
    if not str(file_path).startswith(str(data_dir.resolve())):
        raise HTTPException(403, "Forbidden")
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(
        file_path,
        filename=att["original_filename"],
        media_type=att["mime"] or "application/octet-stream",
    )

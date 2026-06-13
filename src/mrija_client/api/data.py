from __future__ import annotations
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
async def search(q: str = ""):
    from mrija_client.server import get_state
    state = get_state()
    emails = state.db.search(q) if state.db and q.strip() else []
    return _render("search_results.html", emails=emails)


@router.get("/browse", response_class=HTMLResponse)
async def browse(mailbox: str = ""):
    from mrija_client.server import get_state
    state = get_state()
    emails = state.db.browse(mailbox or None) if state.db else []
    return _render("browse.html", emails=emails, mailbox=mailbox)


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


@router.get("/attachment/{sha256}")
async def download_attachment(sha256: str):
    from mrija_client.server import get_state
    state = get_state()
    if not state.db:
        raise HTTPException(503, "No database loaded")
    att = state.db.get_attachment_by_sha256(sha256)
    if not att:
        raise HTTPException(404, "Attachment not found")
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

"""
src/web/routes/auth_routes.py — Login and logout pages.

These are the only routes that don't require authentication.
The login form checks the password and creates a session cookie.
Logout destroys the session and redirects to /login.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from src.web import auth

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Show the login form.

    If the user is already logged in, redirect straight to the archive.
    """
    # Peek at the session without raising an error
    if request.cookies.get(auth.COOKIE_NAME):
        try:
            auth.require_login(request)
            return RedirectResponse(url="/", status_code=303)
        except Exception:
            pass  # cookie exists but is invalid — fall through to login form

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def do_login(
    request: Request,
    password: str = Form(...),  # '...' means the field is required
):
    """Process the login form.

    Checks the submitted password against both the admin and coworker hashes.
    On success: sets the session cookie and redirects to /.
    On failure: re-renders the form with an error message.
    """
    cfg = request.app.state.cfg
    templates = request.app.state.templates

    # Try admin password first, then coworker.
    # We create a fresh Response here because we need to set a cookie on it
    # AND return an HTML redirect — RedirectResponse handles both.
    if cfg.auth_enabled and auth.verify_password(password, cfg.admin_password_hash):
        redir = RedirectResponse(url="/", status_code=303)
        auth.create_session(redir, role="admin")
        return redir

    if cfg.auth_enabled and auth.verify_password(password, cfg.coworker_password_hash):
        redir = RedirectResponse(url="/", status_code=303)
        auth.create_session(redir, role="coworker")
        return redir

    if not cfg.auth_enabled:
        # Auth disabled (Windows client mode): auto-login as coworker.
        redir = RedirectResponse(url="/", status_code=303)
        auth.create_session(redir, role="coworker")
        return redir

    # Wrong password — redisplay the form with an error.
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Falsches Passwort."},
        status_code=401,
    )


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Log out: delete the session and redirect to /login."""
    auth.delete_session(request, response)
    # We need to set the delete-cookie header on a new response.
    redir = RedirectResponse(url="/login", status_code=303)
    auth.delete_session(request, redir)
    return redir

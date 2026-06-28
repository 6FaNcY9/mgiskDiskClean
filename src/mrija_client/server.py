from __future__ import annotations
import os
import secrets as _secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Template
from mrija_client.state import AppState

_HERE = Path(__file__).parent
STATIC_DIR = _HERE / "static"

_app_state: AppState | None = None
_SESSIONS: set[str] = set()

_PUBLIC_PATHS = {"/login"}
_PUBLIC_PREFIXES = ("/static/", "/api/")


def _request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return getattr(request.client, "host", "?")


def _request_ua(request: Request) -> str:
    return request.headers.get("user-agent", "")


def get_state() -> AppState:
    assert _app_state is not None, "call create_app first"
    return _app_state


def _auto_sync_loop(state: AppState) -> None:
    interval_h = int(os.environ.get("MRIJA_SYNC_INTERVAL_HOURS", "24"))
    remote = os.environ.get("MRIJA_SYNC_REMOTE", "")
    if not remote or interval_h <= 0:
        return
    state.log(f"Auto-sync: every {interval_h}h from {remote}")
    while True:
        time.sleep(interval_h * 3600)
        from mrija_client.api.control import _run_sync_impl
        _run_sync_impl(state)


def create_app(state: AppState, mode: str = "user") -> FastAPI:
    global _app_state
    _app_state = state
    state.mode = mode

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        import threading
        t = threading.Thread(target=_auto_sync_loop, args=(state,), daemon=True)
        t.start()
        yield

    app = FastAPI(title="MrijaArchive", docs_url="/api/docs", openapi_url="/openapi.json",
                  lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        sid = request.cookies.get("mrija_sid")
        if not sid or sid not in _SESSIONS:
            if path != "/login":
                state.log_audit(
                    "auth_required",
                    f"Redirected unauthenticated request to {path}",
                    ip=_request_ip(request),
                    ua=_request_ua(request),
                    method=request.method,
                    path=path,
                )
            return RedirectResponse("/login", status_code=303)
        state.touch_session(sid, ip=_request_ip(request), ua=_request_ua(request))
        return await call_next(request)

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        t0 = time.monotonic()
        response = await call_next(request)
        ms = int((time.monotonic() - t0) * 1000)
        path = request.url.path
        if not path.startswith("/static") and path != "/api/update/progress":
            ip = _request_ip(request)
            ua = _request_ua(request)
            state.log(f"{request.method} {path} → {response.status_code} ({ms}ms) [{ip}]")
            state.log_request(ip, request.method, path, response.status_code, ms, ua)
        return response

    try:
        from mrija_client.api.data import router as data_router
        app.include_router(data_router, prefix="/data")
    except ImportError:
        pass

    try:
        from mrija_client.api.control import router as control_router
        app.include_router(control_router, prefix="/api")
    except ImportError:
        pass

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(error: str = ""):
        tpl = Template((STATIC_DIR / "login.html").read_text(encoding="utf-8"))
        return tpl.render(error=bool(error))

    @app.post("/login")
    async def login_post(request: Request):
        form = await request.form()
        password = str(form.get("password", ""))
        expected = os.environ.get("MRIJA_PASSWORD", "")
        if expected and _secrets.compare_digest(password.encode(), expected.encode()):
            sid = _secrets.token_hex(32)
            _SESSIONS.add(sid)
            state.start_session(sid, ip=_request_ip(request), ua=_request_ua(request))
            state.log_audit(
                "login_success",
                "Admin login succeeded",
                ip=_request_ip(request),
                ua=_request_ua(request),
                session=sid,
            )
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie("mrija_sid", sid, httponly=True, samesite="strict", max_age=86400 * 7)
            return resp
        state.log_audit(
            "login_failed",
            "Admin login failed",
            ip=_request_ip(request),
            ua=_request_ua(request),
        )
        return RedirectResponse("/login?error=1", status_code=303)

    @app.get("/logout")
    async def logout(request: Request):
        sid = request.cookies.get("mrija_sid")
        _SESSIONS.discard(sid)
        session = state.end_session(sid)
        state.log_audit(
            "logout",
            "Admin logged out",
            ip=_request_ip(request),
            ua=_request_ua(request),
            session=sid or "",
            session_requests=session.get("requests", 0) if session else 0,
        )
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("mrija_sid")
        return resp

    @app.get("/", response_class=HTMLResponse)
    async def index():
        tpl = Template((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
        return tpl.render(
            api_key=os.environ.get("MRIJA_API_KEY", "dev-key"),
            mode=mode,
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        if mode != "admin":
            from fastapi import Response
            return Response(status_code=404)
        tpl = Template((STATIC_DIR / "admin.html").read_text(encoding="utf-8"))
        return tpl.render(
            api_key=os.environ.get("MRIJA_API_KEY", "dev-key"),
            db_path=str(state.db_path) if state.db_path else "no database",
            droplet_url=os.environ.get("MRIJA_DROPLET_URL", ""),
            has_droplet=bool(os.environ.get("MRIJA_DROPLET_URL") and os.environ.get("MRIJA_DROPLET_KEY")),
            sync_configured=bool(os.environ.get("MRIJA_SYNC_REMOTE")),
            last_sync_at=state.last_sync_at,
            last_sync_ok=state.last_sync_ok,
        )

    return app

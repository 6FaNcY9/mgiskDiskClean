from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from jinja2 import Template
from mrija_client.state import AppState

_HERE = Path(__file__).parent
STATIC_DIR = _HERE / "static"

_app_state: AppState | None = None


def get_state() -> AppState:
    assert _app_state is not None, "call create_app first"
    return _app_state


def create_app(state: AppState, mode: str = "user") -> FastAPI:
    global _app_state
    _app_state = state
    state.mode = mode

    app = FastAPI(title="MrijaArchive", docs_url="/api/docs", openapi_url="/openapi.json")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if not path.startswith("/static") and path != "/api/update/progress":
            state.log(
                f"[dim]{request.method}[/dim] {path}"
                f" [dim]→ {response.status_code}[/dim]"
            )
        return response

    # Routers imported lazily — they don't exist until Tasks 6 and 7
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
        droplet_url = os.environ.get("MRIJA_DROPLET_URL", "")
        droplet_key = os.environ.get("MRIJA_DROPLET_KEY", "")
        return tpl.render(
            api_key=os.environ.get("MRIJA_API_KEY", "dev-key"),
            db_path=str(state.db_path) if state.db_path else "no database",
            droplet_url=droplet_url,
            has_droplet=bool(droplet_url and droplet_key),
        )

    return app

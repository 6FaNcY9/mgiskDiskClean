from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from mrija_client.state import AppState

_HERE = Path(__file__).parent
STATIC_DIR = _HERE / "static"

_app_state: AppState | None = None


def get_state() -> AppState:
    assert _app_state is not None, "call create_app first"
    return _app_state


def create_app(state: AppState) -> FastAPI:
    global _app_state
    _app_state = state

    app = FastAPI(title="MrijaArchive", docs_url="/api/docs", openapi_url="/openapi.json")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return app

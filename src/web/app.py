"""
src/web/app.py — FastAPI application factory.

A "factory function" (create_app) is a pattern where instead of creating the
app at module level, you create it inside a function.  This lets tests create
a fresh app with different settings, and lets uvicorn use the --factory flag.

How to run in development:
    MRIJA_SQLITE_PATH=/path/to/mail_archive.sqlite \
    MRIJA_DATA_DIR=/path/to/data \
    uvicorn src.web.app:create_app --factory --reload --host 127.0.0.1 --port 8080

How to run from the Windows launcher (app.py):
    subprocess.Popen([sys.executable, "-m", "uvicorn",
                      "src.web.app:create_app",
                      "--factory", "--host", "127.0.0.1", "--port", "8080"])
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.web import auth, db
from src.web.config import Config

# ── Template directory ────────────────────────────────────────────────────────
# __file__ is  src/web/app.py  →  parent is  src/web/  →  templates/ lives there.
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Called by uvicorn via  --factory  flag.
    Also called in tests to get a fresh app per test.
    """
    cfg = Config.from_env()

    # ── Lifespan: startup + shutdown logic ────────────────────────────────────
    # The @asynccontextmanager lifespan replaces the old @app.on_event("startup")
    # pattern.  Code before `yield` runs on startup; code after runs on shutdown.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: initialise database and auth
        db.set_db_path(cfg.sqlite_path)
        db.init_db(cfg.sqlite_path)
        auth.configure(cfg.secret_key, cfg.session_lifetime)

        # Attach config to the app state so routes can read it via request.app.state.cfg
        app.state.cfg = cfg
        app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

        yield  # server is running; handle requests

        # Shutdown: nothing to clean up for SQLite

    app = FastAPI(
        title="Mrija Archive",
        lifespan=lifespan,
        # Disable the automatic /docs and /redoc pages in production
        # (they expose the API structure; not needed for a local app).
        docs_url="/docs" if cfg.debug else None,
        redoc_url=None,
    )

    # ── Static files ──────────────────────────────────────────────────────────
    # Serve CSS, JS, and images from  src/web/static/  at  /static/
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── Routes ────────────────────────────────────────────────────────────────
    # Import here (not at module top) to avoid circular imports while the
    # package is still being built.  Each router file registers its own paths.
    from src.web.routes import auth_routes, download, log, review, search, updates

    app.include_router(auth_routes.router)
    app.include_router(search.router)
    app.include_router(download.router)
    app.include_router(review.router)
    app.include_router(updates.router)
    app.include_router(log.router)

    # ── Security middleware ───────────────────────────────────────────────────
    # Set Cache-Control: no-store on every response so the embedded pywebview
    # browser (and any other browser) never caches pages with sensitive data.
    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # ── 401 handler: redirect to login ────────────────────────────────────────
    # When a route raises HTTP 401, send the user to /login instead of showing
    # a raw JSON error.
    @app.exception_handler(401)
    async def redirect_to_login(request: Request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=303)

    return app

"""
src/web/config.py — Application configuration.

All settings come from environment variables so the same Python code works
both on Linux (development) and inside the Windows PyInstaller exe (production).
The launcher sets MRIJA_SQLITE_PATH and MRIJA_DATA_DIR before starting the server.

Usage:
    from src.web.config import Config
    cfg = Config.from_env()        # reads os.environ
    print(cfg.sqlite_path)         # Path to the SQLite database
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """All runtime configuration in one place.

    Every field has a default so the server can start without any environment
    variables set (useful for quick dev smoke-tests).  In real deployments the
    launcher or a .env file supplies the important values.
    """

    # ── Database ──────────────────────────────────────────────────────────────
    # Path to the SQLite file that contains all mail data.
    # The launcher sets this via the MRIJA_SQLITE_PATH env var.
    sqlite_path: Path = field(default_factory=lambda: Path("data/client/mail_archive.sqlite"))

    # Root of the data directory — used to resolve attachment file paths safely.
    # Attachments live under  data_dir / mailboxes / <name> / attachments /
    data_dir: Path = field(default_factory=lambda: Path("data"))

    # ── Authentication ────────────────────────────────────────────────────────
    # BCrypt hashes of the admin and coworker passwords.
    # Generate with:  python -c "import bcrypt; print(bcrypt.hashpw(b'pw', bcrypt.gensalt()).decode())"
    admin_password_hash: str = ""
    coworker_password_hash: str = ""

    # When True the login page is skipped entirely (useful for local Windows client
    # where no one else has network access to the loopback server).
    auth_enabled: bool = True

    # ── Session security ──────────────────────────────────────────────────────
    # 32-byte hex string used to sign session cookies and CSRF tokens.
    # A random default is generated at startup — this means sessions are
    # invalidated on every restart, which is acceptable for a local app.
    # In a real multi-user deployment set this to a stable value in the environment.
    secret_key: str = field(default_factory=lambda: secrets.token_hex(32))

    # How many seconds an idle session stays valid (default: 2 hours)
    session_lifetime: int = 7200

    # ── VirusTotal ────────────────────────────────────────────────────────────
    # Leave empty to disable VirusTotal scanning.
    vt_api_key: str = ""

    # ── Update server ─────────────────────────────────────────────────────────
    # The manifest is fetched by the Python server, not the browser, so the URL
    # lives here (not in the HTML).  This prevents a browser-supplied URL from
    # being used to point the server at a malicious host.
    update_manifest_url: str = ""

    # Optional DigitalOcean logging endpoint
    do_log_url: str = ""
    do_log_token: str = ""

    # ── Application behaviour ─────────────────────────────────────────────────
    # Enable debug mode: uvicorn auto-reload, full tracebacks in error pages.
    debug: bool = False

    # Emails shown per search-result page
    items_per_page: int = 50

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config by reading os.environ.

        Each environment variable name maps to the field name in UPPER_SNAKE_CASE
        with a MRIJA_ prefix, e.g. MRIJA_SQLITE_PATH → sqlite_path.
        """
        def env_path(key: str, default: Path) -> Path:
            v = os.environ.get(key, "")
            return Path(v) if v else default

        def env_str(key: str, default: str = "") -> str:
            return os.environ.get(key, default)

        def env_bool(key: str, default: bool = False) -> bool:
            v = os.environ.get(key, "")
            return v.lower() in ("1", "true", "yes") if v else default

        def env_int(key: str, default: int) -> int:
            v = os.environ.get(key, "")
            return int(v) if v.isdigit() else default

        return cls(
            sqlite_path=env_path("MRIJA_SQLITE_PATH", Path("data/client/mail_archive.sqlite")),
            data_dir=env_path("MRIJA_DATA_DIR", Path("data")),
            admin_password_hash=env_str("MRIJA_ADMIN_HASH"),
            coworker_password_hash=env_str("MRIJA_COWORKER_HASH"),
            # AUTH_ENABLED defaults True; set to "0" or "false" to skip login
            auth_enabled=env_bool("MRIJA_AUTH_ENABLED", default=True),
            secret_key=env_str("MRIJA_SECRET_KEY") or secrets.token_hex(32),
            session_lifetime=env_int("MRIJA_SESSION_LIFETIME", 7200),
            vt_api_key=env_str("MRIJA_VT_API_KEY"),
            update_manifest_url=env_str("MRIJA_UPDATE_URL"),
            do_log_url=env_str("MRIJA_DO_LOG_URL"),
            do_log_token=env_str("MRIJA_DO_LOG_TOKEN"),
            debug=env_bool("MRIJA_DEBUG"),
            items_per_page=env_int("MRIJA_ITEMS_PER_PAGE", 50),
        )

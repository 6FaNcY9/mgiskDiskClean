"""
src/web/auth.py — Session management and CSRF protection.

This replaces two PHP classes:
  - web/src/Auth/SessionManager.php  (login, logout, role check, idle timeout)
  - web/src/Auth/CsrfGuard.php       (token generation and validation)

How it works:
  1. On successful login, we create a random session_id and store the user's
     role in a dict (_sessions).  We sign the session_id with itsdangerous
     (HMAC) and send it to the browser as an HttpOnly cookie.
  2. On every request, the signed cookie is verified and the session looked up.
  3. CSRF tokens are HMAC hashes of the session_id.  Because they're derived
     from the session, an attacker's page cannot forge them (they don't know
     the session_id or the secret key).

Why HttpOnly cookies?
  JavaScript on the page cannot read an HttpOnly cookie, so an XSS attack
  cannot steal the session token.  The cookie is still sent automatically on
  every same-origin request.

Why HMAC for CSRF tokens instead of a stored random value?
  It avoids a server-side storage entry per request/tab.  The math ensures
  the token can only be produced by someone who knows the secret_key.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner


# ── In-process session store ───────────────────────────────────────────────────
# Maps  session_id  →  {'role': 'admin'|'coworker', 'created_at': float}
# This is fine for a single-process local app (one user at a time).
# A multi-user server would use Redis or a DB table instead.
_sessions: dict[str, dict] = {}

# Will be set once at startup from Config.  All HMAC operations use this.
_secret_key: str = ""
_session_lifetime: int = 7200  # seconds


def configure(secret_key: str, session_lifetime: int = 7200) -> None:
    """Initialise the auth module.  Called once inside app lifespan."""
    global _secret_key, _session_lifetime
    _secret_key = secret_key
    _session_lifetime = session_lifetime


# ── Cookie helpers ────────────────────────────────────────────────────────────

COOKIE_NAME = "mrija_session"


def _signer() -> TimestampSigner:
    """Return a TimestampSigner using our secret key.

    TimestampSigner signs a value *and* embeds a timestamp, so we can reject
    cookies that are too old without keeping a server-side expiry record.
    """
    return TimestampSigner(_secret_key)


def create_session(response: Response, role: str) -> str:
    """Create a new session, store it, and set the session cookie.

    Returns the raw session_id (not the signed cookie value).
    """
    session_id = secrets.token_hex(32)
    _sessions[session_id] = {"role": role, "created_at": time.time()}

    # Sign the session_id before putting it in the cookie.
    # The browser stores the signed value; we verify it on each request.
    signed = _signer().sign(session_id).decode()

    # HttpOnly: JS cannot read this cookie (XSS protection).
    # SameSite=Strict: cookie is only sent on same-origin requests (CSRF protection).
    # Secure=False: the server runs on http://localhost so HTTPS isn't available.
    response.set_cookie(
        key=COOKIE_NAME,
        value=signed,
        httponly=True,
        samesite="strict",
        secure=False,
        max_age=_session_lifetime,
    )
    return session_id


def delete_session(request: Request, response: Response) -> None:
    """Log out: remove the session from memory and clear the cookie."""
    session_id = _get_session_id(request)
    if session_id and session_id in _sessions:
        del _sessions[session_id]
    response.delete_cookie(COOKIE_NAME)


def _get_session_id(request: Request) -> Optional[str]:
    """Extract and verify the session cookie.  Returns None if invalid."""
    signed = request.cookies.get(COOKIE_NAME)
    if not signed:
        return None
    try:
        # max_age here is the maximum allowed age of the *signature timestamp*,
        # not the session itself — we check session age separately below.
        session_id = _signer().unsign(signed, max_age=_session_lifetime).decode()
    except (BadSignature, SignatureExpired):
        # Cookie was tampered with or is too old.
        return None
    return session_id


def _get_session(request: Request) -> Optional[dict]:
    """Return the session dict for the current request, or None."""
    session_id = _get_session_id(request)
    if session_id is None:
        return None
    session = _sessions.get(session_id)
    if session is None:
        return None
    # Check idle timeout: reject sessions that haven't been used recently.
    if time.time() - session["created_at"] > _session_lifetime:
        del _sessions[session_id]
        return None
    # Refresh the activity timestamp so active users don't get logged out.
    session["created_at"] = time.time()
    return session


# ── FastAPI dependencies ──────────────────────────────────────────────────────
# These are injected into route functions with  = Depends(require_login)  etc.

def get_current_session(request: Request) -> Optional[dict]:
    """Return the session dict or None.  Doesn't raise — use in pages that
    behave differently when logged in vs. not."""
    return _get_session(request)


def require_login(request: Request) -> dict:
    """FastAPI dependency: raise 401 if not authenticated.

    Routes that need a logged-in user add  session: dict = Depends(require_login)
    to their signature.
    """
    session = _get_session(request)
    if session is None:
        # Return 401; the browser (or pywebview) will redirect to /login.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"X-Redirect": "/login"},
        )
    return session


def require_admin(request: Request) -> dict:
    """FastAPI dependency: raise 403 if not admin."""
    session = require_login(request)
    if session.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return session


# ── Password verification ─────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a BCrypt hash.

    The PHP side generated hashes with  password_hash('pw', PASSWORD_BCRYPT).
    Python's bcrypt library is compatible with PHP's BCrypt output.

    Returns False (instead of raising) when the hash is empty or invalid —
    this makes the login form show "wrong password" instead of a 500 error.
    """
    if not hashed:
        return False
    try:
        import bcrypt  # imported here so tests can patch it easily
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── CSRF protection ───────────────────────────────────────────────────────────

def generate_csrf_token(session_id: str) -> str:
    """Generate a CSRF token for the current session.

    The token is an HMAC of the session_id using our secret key.
    Because the session_id is random and the secret key is unknown to
    attackers, they cannot compute a valid token from another origin.
    """
    return hmac.new(
        _secret_key.encode(),
        session_id.encode(),
        hashlib.sha256,
    ).hexdigest()


def get_csrf_token(request: Request) -> str:
    """Return the CSRF token for the current session.

    Returns an empty string if there's no valid session (token won't validate).
    Use this in templates:  {{ csrf_token }}
    """
    session_id = _get_session_id(request)
    if not session_id:
        return ""
    return generate_csrf_token(session_id)


async def validate_csrf(request: Request) -> None:
    """FastAPI dependency: raise 403 if the CSRF token is missing or wrong.

    Reads the token from:
      1. The POST body field  csrf_token
      2. The  X-CSRF-Token  HTTP header (for JavaScript fetch() calls)

    Use on any route that changes server state (POST, DELETE, etc.):
        @router.post("/api/review-decision")
        async def save_decision(..., _csrf=Depends(validate_csrf)):
            ...
    """
    session_id = _get_session_id(request)
    if not session_id:
        raise HTTPException(status_code=403, detail="No session")

    expected = generate_csrf_token(session_id)

    # Try the header first (AJAX calls use this)
    token = request.headers.get("X-CSRF-Token", "")

    # Fall back to form body (traditional HTML form submissions)
    if not token:
        form = await request.form()
        token = str(form.get("csrf_token", ""))

    # Use hmac.compare_digest instead of == to prevent timing attacks.
    # A timing attack measures how long the comparison takes to guess the token
    # character by character.  compare_digest always takes the same time.
    if not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")

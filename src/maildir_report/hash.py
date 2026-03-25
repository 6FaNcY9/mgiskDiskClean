"""
hash.py — Collision-resistant content hashing for maildir_report.

Design rules
------------
- SHA-256 only.  No MD5, no SHA-1.
- Deterministic: same bytes → same digest, always.
- Zero-byte payloads are valid input (hash of empty bytes is defined).
- Accepts bytes, bytearray, or None (None treated as empty).

Public API
----------
sha256_hex(payload) -> str
    Return the lowercase hex SHA-256 digest of *payload*.
"""

from __future__ import annotations

import hashlib


def sha256_hex(payload: bytes | bytearray | None) -> str:
    """Return lowercase hex SHA-256 digest of *payload*.

    Parameters
    ----------
    payload:
        Raw bytes to hash.  ``None`` and empty ``bytes`` both produce the
        SHA-256 digest of zero bytes (the well-known empty-string hash).

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest.

    Examples
    --------
    >>> sha256_hex(b"hello")
    '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    >>> sha256_hex(b"") == sha256_hex(None)
    True
    """
    if payload is None:
        payload = b""
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(
            f"sha256_hex expects bytes, bytearray, or None; got {type(payload).__name__!r}"
        )
    return hashlib.sha256(payload).hexdigest()

"""
ids.py — Stable, deterministic identifiers for maildir_report.

Design rules (anti-pattern reference: ``scripts/maildir_viewer.py`` line 271)
---------------------------------------------------------------------------
  WRONG:  m["id"] = i                          # index-based — breaks on re-scan
  WRONG:  str(uuid.uuid4())                    # random — non-deterministic
  WRONG:  datetime.now().isoformat()           # runtime timestamp — non-deterministic

  CORRECT (this module):
    email_stable_id  — SHA-256 of (filepath + message_id)
    part_stable_id   — SHA-256 of part payload bytes
    dup_group_stable_id — SHA-256 of sorted member email stable IDs

All IDs are lowercase hex SHA-256 strings (64 chars).
No salting, no timestamps, no random inputs.  Same data → same ID, always.
"""

from __future__ import annotations

import hashlib
from typing import Any


def _sha256_hex(*parts: str | bytes) -> str:
    """Return lowercase hex SHA-256 digest of the concatenation of *parts*.

    String values are UTF-8 encoded; bytes values are used directly.
    A null byte (b"\\x00") is used as a separator between parts to prevent
    collisions like sha256("ab" + "c") == sha256("a" + "bc").
    """
    h = hashlib.sha256()
    for i, part in enumerate(parts):
        if i > 0:
            h.update(b"\x00")  # separator — prevents prefix collisions
        if isinstance(part, str):
            h.update(part.encode("utf-8"))
        else:
            h.update(part)
    return h.hexdigest()


def email_stable_id(record: dict[str, Any]) -> str:
    """Return a stable ID for an email record.

    Derived from: ``filepath`` (primary) + ``message_id`` (secondary).

    ``filepath`` is the absolute path to the Maildir file on disk — unique
    within a single Maildir scan.  ``message_id`` is the RFC 2822 Message-ID
    header value, included as a secondary discriminator.

    Both fields must be present in *record*.  They must NOT change between
    scans of the same Maildir for the ID to remain stable.

    Parameters
    ----------
    record:
        A dict with at least ``"filepath"`` and ``"message_id"`` keys.

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest.
    """
    filepath = str(record["filepath"])
    message_id = str(record.get("message_id", ""))
    return _sha256_hex(filepath, message_id)


def part_stable_id(part: dict[str, Any]) -> str:
    """Return a stable ID for a MIME part.

    Derived from: the raw decoded payload bytes (content-addressable).

    If the part's ``payload_bytes`` field is set, its SHA-256 is used directly.
    This is a *content hash* — same bytes across different emails produce the
    same part ID, which is intentional (enables duplicate detection).

    Parameters
    ----------
    part:
        A dict with at least a ``"payload_bytes"`` key (bytes or None).

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest.  For a None/empty payload
        the ID is still deterministic (hash of empty bytes).
    """
    payload = part.get("payload_bytes") or b""
    if not isinstance(payload, (bytes, bytearray)):
        payload = str(payload).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def dup_group_stable_id(member_email_ids: list[str]) -> str:
    """Return a stable ID for a duplicate group.

    Derived from: the sorted list of member email stable IDs.
    Sorting makes this order-invariant — the same set of members always
    produces the same group ID regardless of the order they were passed in.

    Parameters
    ----------
    member_email_ids:
        List of email stable IDs (as returned by ``email_stable_id()``).

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest.
    """
    # Sort to make the result independent of input order
    sorted_ids = sorted(member_email_ids)
    # Join with null-byte separator (same strategy as _sha256_hex)
    canonical = "\x00".join(sorted_ids)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

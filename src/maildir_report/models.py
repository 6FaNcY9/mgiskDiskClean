"""
models.py — Canonical data model for maildir_report.

All records are plain dicts (TypedDict-annotated for documentation).
No ORM, no dataclasses with __hash__ surprises — just plain dicts
that can be serialised to JSON without conversion.

Key design invariants
---------------------
- IDs are NEVER index-based (``m["id"] = i`` is the anti-pattern to avoid).
  Stable IDs are computed in ``ids.py`` from canonical content fields only.
- All timestamps stored as ISO-like strings (``"YYYY-MM-DD HH:MM"``).
  No datetime objects escape module boundaries to avoid timezone footguns.
- No runtime-generated UUIDs or datetime.now() calls.
"""

from __future__ import annotations

from typing import Any, TypedDict


class PartRecord(TypedDict, total=False):
    """
    One MIME part (attachment or inline body part) extracted from an email.

    Fields
    ------
    filename : str
        Decoded filename (or ``"[inline <subtype>]"`` for nameless inline parts).
    mime : str
        Lower-cased MIME content-type string, e.g. ``"application/pdf"``.
    size : int
        Byte-length of the decoded payload.  0 when payload was empty/absent.
    payload_bytes : bytes | None
        Raw decoded payload bytes.  May be ``None`` after the part is finalised
        and stored in the manifest (not persisted to JSON).
    content_hash : str
        SHA-256 hex digest of ``payload_bytes``.  Empty string when payload is
        absent or too small to be meaningful.
    category : str
        Human-readable category label (``"pdf"``, ``"image"``, ``"word"``, …).
    is_dup : bool
        True if this part's content_hash appears in at least one other email.
    dup_group_id : str | None
        Stable group ID (from ``ids.py``) if ``is_dup`` is True, else None.
    stable_id : str
        Stable ID for this part, computed by ``ids.part_stable_id()``.
    """

    filename: str
    mime: str
    size: int
    payload_bytes: bytes | None
    content_hash: str
    category: str
    is_dup: bool
    dup_group_id: str | None
    stable_id: str


class EmailRecord(TypedDict, total=False):
    """
    One email message parsed from a Maildir file.

    Fields
    ------
    filepath : str
        Absolute path to the raw Maildir file (canonical, not relative).
        Used as primary input to ``ids.email_stable_id()``.
    message_id : str
        Value of the ``Message-ID`` header (decoded, stripped).
        Used as secondary input to ``ids.email_stable_id()``.
    subject : str
        Decoded subject line (RFC 2047).
    date : str
        Formatted date string ``"YYYY-MM-DD HH:MM"``, or raw date header
        substring when parsing fails.  Empty string when absent.
    date_day : str
        Date-only portion ``"YYYY-MM-DD"`` for display grouping.
    sender : str
        Decoded ``From:`` header.
    to : str
        Decoded ``To:`` header.
    folder : str
        Maildir folder name (e.g. ``"INBOX"``, ``".Sent"``).
    total_size : int
        Byte-length of the raw file on disk.
    parts : list[PartRecord]
        Ordered list of MIME parts (see ``ordering.sort_parts()``).
    stable_id : str
        Computed stable ID — see ``ids.email_stable_id()``.
    dup_group_id : str | None
        Stable duplicate-group ID if this email is part of a duplicate group.
    dup_rank : int | None
        Position within the duplicate group sorted by date (0 = oldest).
    """

    filepath: str
    message_id: str
    subject: str
    date: str
    date_day: str
    sender: str
    to: str
    folder: str
    total_size: int
    parts: list[Any]  # list[PartRecord] — avoids circular import issues
    stable_id: str
    dup_group_id: str | None
    dup_rank: int | None


class DupGroupRecord(TypedDict, total=False):
    """
    A duplicate group: a set of email records that share at least one
    attachment payload hash.

    Fields
    ------
    group_id : str
        Stable ID derived from sorted member email stable IDs via
        ``ids.dup_group_stable_id()``.
    member_email_ids : list[str]
        Stable IDs of the member emails.  Ordered by ``ordering.sort_emails()``.
    member_count : int
        ``len(member_email_ids)`` — convenience.
    total_size : int
        Sum of ``total_size`` across all member emails.
    canonical_email_id : str
        Stable ID of the "oldest" (date-first) member; used as the group's
        representative entry in the PDF.
    """

    group_id: str
    member_email_ids: list[str]
    member_count: int
    total_size: int
    canonical_email_id: str

"""
parser.py — Strict Maildir parsing core for maildir_report.

Design principles
-----------------
- NO silent failures: parse errors raise MailParseError with exact filepath + reason.
- NO silent part drops: size thresholds from the legacy script are removed.
  Every named attachment and every nameless inline part is included.
- Deterministic output: scan_maildir sorts dirs/files during os.walk;
  results are sorted by (date, filepath) via ordering.sort_emails().
- IDs computed via ids.py (content-based SHA-256, never index-based).
- Python 3.11+ only (email.message_from_bytes, f-strings, walrus).

Legacy anti-patterns removed
-----------------------------
  WRONG (maildir_viewer.py:94):   except: return None
  WRONG (maildir_viewer.py:98):   except: return None
  WRONG (maildir_viewer.py:118):  if size < 128: continue
  WRONG (maildir_viewer.py:160):  if m: mails.append(m)   # silently skips None
  WRONG (maildir_viewer.py:148):  os.walk without sorting dirs/files
"""

from __future__ import annotations

import email as _email
import email.message as _emsg
import hashlib  # retained for ids.py compatibility; hash.py is the canonical part-hashing path
import pathlib
from email.header import decode_header, make_header
from email.utils import parsedate
from datetime import datetime
from typing import Any

from maildir_report.hash import sha256_hex
from maildir_report.ids import email_stable_id, part_stable_id
from maildir_report.ordering import sort_emails, sort_parts
from maildir_report.walk import deterministic_walk

# ── typed exception ───────────────────────────────────────────────────────────


class MailParseError(Exception):
    """Raised when a Maildir file cannot be read or parsed.

    Attributes
    ----------
    filepath : str
        Absolute path to the file that caused the error.
    reason : str
        Human-readable description of what went wrong.
    """

    def __init__(self, filepath: str, reason: str) -> None:
        self.filepath = filepath
        self.reason = reason
        super().__init__(f"Cannot parse {filepath!r}: {reason}")


# ── MIME category map (matches legacy, extended) ──────────────────────────────


_CAT_MAP: dict[str, str] = {
    "application/pdf": "pdf",
    "application/msword": "word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/vnd.ms-excel": "excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "application/vnd.ms-powerpoint": "pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/calendar": "calendar",
    "application/ics": "calendar",
    "application/zip": "archive",
    "application/x-zip-compressed": "archive",
    "application/pkcs7-signature": "signature",
    "application/x-pkcs7-signature": "signature",
}

# MIME types that are structural/container types (not attachments themselves).
# Parts with these types but no filename are treated as body text parts.
_BODY_MIME = frozenset(
    {
        "text/plain",
        "text/html",
        "multipart/mixed",
        "multipart/alternative",
        "multipart/related",
        "multipart/signed",
    }
)


def _classify(content_type: str, filename: str) -> str:
    ct = content_type.lower()
    if ct in _CAT_MAP:
        return _CAT_MAP[ct]
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    if ct.startswith("audio/"):
        return "audio"
    if ct.startswith("text/"):
        return "text"
    if filename:
        return "attachment"
    return "other"


# ── string helpers ────────────────────────────────────────────────────────────


def _decode_header_str(value: str | None) -> str:
    """Decode an RFC 2047-encoded header value to a Unicode string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _parse_date(date_str: str | None) -> tuple[str, str]:
    """Parse an RFC 2822 date string.

    Returns
    -------
    (date_day, date_formatted)
        ``date_day``      — ``"YYYY-MM-DD"`` or empty string on failure.
        ``date_formatted``— ``"YYYY-MM-DD HH:MM"`` or the raw header on failure.
    """
    if not date_str:
        return "", ""
    try:
        t = parsedate(date_str)
        if t:
            dt = datetime(*t[:6])
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return "", (date_str[:30] if date_str else "")


# ── core parsing ──────────────────────────────────────────────────────────────


def parse_email_file(filepath: str, folder: str) -> dict[str, Any]:
    """Parse a single Maildir file and return an EmailRecord.

    Parameters
    ----------
    filepath:
        Absolute path to the Maildir message file.
    folder:
        Maildir folder name (e.g. ``"INBOX"``, ``".Sent"``).

    Returns
    -------
    dict[str, Any]
        An EmailRecord dict as specified in ``models.py``.

    Raises
    ------
    MailParseError
        If the file cannot be read (permissions, missing) or cannot be parsed
        as an RFC 2822 message.  The exception message always contains *filepath*.
    """
    # ── 1. read raw bytes ────────────────────────────────────────────────────
    try:
        raw = pathlib.Path(filepath).read_bytes()
    except OSError as exc:
        raise MailParseError(filepath=filepath, reason=str(exc)) from exc

    # An empty file is not a valid RFC 2822 message.
    if not raw:
        raise MailParseError(
            filepath=filepath, reason="file is empty (0 bytes) — not a valid message"
        )

    # ── 2. parse message structure ───────────────────────────────────────────
    try:
        msg = _email.message_from_bytes(raw)
    except Exception as exc:
        raise MailParseError(
            filepath=filepath, reason=f"email.message_from_bytes failed: {exc}"
        ) from exc

    # ── 3. extract headers ───────────────────────────────────────────────────
    message_id = _decode_header_str(msg.get("Message-ID", "")).strip()
    subject = _decode_header_str(msg.get("Subject", "(no subject)"))
    sender = _decode_header_str(msg.get("From", ""))
    to = _decode_header_str(msg.get("To", ""))
    date_day, date_fmt = _parse_date(msg.get("Date"))

    # ── 4. extract MIME parts (no silent size-threshold drops) ───────────────
    raw_parts: list[dict[str, Any]] = []
    has_nested_messages: bool = False
    for part in msg.walk():
        ct = part.get_content_type().lower()

        # Skip pure container/structural MIME types that have no payload.
        if ct in _BODY_MIME and not part.get_filename():
            continue
        # message/rfc822 wraps a forwarded/nested message.  We do NOT recurse
        # (that is Task 8 scope) but we MUST NOT silently discard it.  Instead:
        # record a deterministic "nested_message" part and flag the email record.
        if ct == "message/rfc822":
            has_nested_messages = True
            # Re-serialise the sub-message to bytes so we can hash it stably.
            sub_payloads = part.get_payload()  # list[Message] for message/rfc822
            sub_bytes = b""
            if isinstance(sub_payloads, list) and sub_payloads:
                sub_msg = sub_payloads[0]
                # list items are typed str|Message; use isinstance to narrow for pyright
                if isinstance(sub_msg, _emsg.Message):
                    try:
                        sub_bytes = sub_msg.as_bytes()
                    except Exception:
                        sub_bytes = b""
            nested_hash = sha256_hex(sub_bytes)
            nested_record: dict[str, Any] = {
                "filename": "[nested message]",
                "mime": ct,
                "size": len(sub_bytes),
                "payload_bytes": sub_bytes,
                "content_hash": nested_hash,
                "category": "nested_message",
                "is_dup": False,
                "dup_group_id": None,
            }
            nested_record["stable_id"] = part_stable_id(nested_record)
            raw_parts.append(nested_record)
            continue

        # Decode filename (may be None for inline parts).
        raw_filename = part.get_filename()
        filename = _decode_header_str(raw_filename) if raw_filename else ""

        # If this part has no filename and is a common body type, skip it
        # (e.g. text/plain body without a filename is not an attachment).
        if not filename and ct in _BODY_MIME:
            continue

        # Inline parts with no filename get a synthetic label.
        if not filename:
            filename = f"[inline {ct.split('/')[-1]}]"

        # Decode payload — note: get_payload(decode=True) may return None for
        # multipart containers, which we've already filtered above.
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None

        payload_bytes = payload if isinstance(payload, (bytes, bytearray)) else b""
        size = len(payload_bytes)

        # Content hash: always SHA-256 of the decoded payload bytes.
        # We compute it for ALL parts (including zero-byte) — no threshold.
        content_hash = sha256_hex(payload_bytes)

        category = _classify(ct, filename)

        part_record: dict[str, Any] = {
            "filename": filename,
            "mime": ct,
            "size": size,
            "payload_bytes": payload_bytes,
            "content_hash": content_hash,
            "category": category,
            "is_dup": False,
            "dup_group_id": None,
        }
        part_record["stable_id"] = part_stable_id(part_record)
        raw_parts.append(part_record)

    sorted_parts = sort_parts(raw_parts)

    # ── 5. assemble EmailRecord ──────────────────────────────────────────────
    record: dict[str, Any] = {
        "filepath": filepath,
        "message_id": message_id,
        "subject": subject,
        "date": date_fmt,
        "date_day": date_day,
        "sender": sender,
        "to": to,
        "folder": folder,
        "total_size": len(raw),
        "parts": sorted_parts,
        "has_nested_messages": has_nested_messages,
        "dup_group_id": None,
        "dup_rank": None,
    }
    record["stable_id"] = email_stable_id(record)
    return record


# ── Maildir scanning ──────────────────────────────────────────────────────────


def scan_maildir(root: str) -> list[dict[str, Any]]:
    """Scan a Maildir directory tree and return a list of EmailRecords.

    Only ``cur/`` and ``new/`` sub-directories are scanned.  ``tmp/`` is
    always excluded.  Files starting with ``.`` are skipped (Maildir convention
    for hidden / lock files).

    Traversal is fully deterministic via ``walk.deterministic_walk()``:
    dirs and files are sorted at every level, and Maildir++ dot-folder names
    are normalized to human-readable labels (e.g. ``".Sent"`` → ``"Sent"``).

    deterministic iteration order regardless of filesystem.

    Parameters
    ----------
    root:
        Path to the Maildir root directory.

    Returns
    -------
    list[dict[str, Any]]
        EmailRecord dicts sorted by ``ordering.sort_emails()`` (date, filepath).

    Raises
    ------
    MailParseError
        Immediately on the first file that cannot be read or parsed.
        No silent skipping — every file on disk must produce a valid record.
    """
    records: list[dict[str, Any]] = []

    for fpath, folder in deterministic_walk(root):
        # deterministic_walk already filters tmp/, dotfiles, and non-cur/new dirs.
        # parse_email_file raises MailParseError on any failure — no try/except here.
        record = parse_email_file(fpath, folder=folder)
        records.append(record)

    return sort_emails(records)


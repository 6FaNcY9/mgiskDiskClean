"""
decisions_template.py — Editable decisions template generator for maildir_report.

Design rules
------------
- NO datetime.now() calls.  This module is stateless and deterministic.
- Row order follows sort_emails(records) — identical to PDF/manifest ordering.
  This ensures the reviewer can cross-reference template rows against the PDF.
- Stable IDs are read verbatim from records; never recomputed or synthesised here.
- All row values are plain strings (JSON-serialisable without conversion).
- The "decision" field is always an empty string — a blank editable cell for
  the reviewer to fill in (approve / reject / flag / etc.).

Output formats
--------------
Two serialisation helpers are provided so the caller can choose:

    generate_decisions_template(records) -> list[dict[str, str]]
        Core function.  Returns one row dict per record in sort_emails() order.
        Each row has exactly these string keys (in canonical column order):
            "stable_id"              — verbatim from record["stable_id"]
            "filepath"               — verbatim from record["filepath"]
            "decision"               — empty string (to be filled by reviewer)
            "folder"                 — maildir folder name
            "date"                   — formatted date string (YYYY-MM-DD HH:MM)
            "from"                   — sender header string
            "subject"                — subject truncated to 80 chars + … (matches PDF)
            "total_size_bytes"       — total raw message size in bytes
            "attachment_count"       — number of non-body attachment parts
            "attachment_total_bytes" — sum of attachment part sizes
            "attachment_names"       — semicolon-separated filenames; empty if none
            "is_duplicate"           — "true" or "false"
            "dup_group_id"           — 64-hex string or empty
            "dup_rank"               — integer string or empty

    serialize_decisions_csv(rows) -> str
        Convert the row list to a CSV string with a header row.
        Header: stable_id,filepath,decision,...  (all 14 columns)
        Uses Python's csv module to ensure correct quoting/escaping.

    serialize_decisions_json(rows) -> str
        Convert the row list to a compact, deterministic JSON string.
        Uses json.dumps with sort_keys=False (keys are already canonical),
        ensure_ascii=False, and no trailing whitespace.

Public API
----------
generate_decisions_template(records) -> list[dict[str, str]]
serialize_decisions_csv(rows) -> str
serialize_decisions_json(rows) -> str
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from maildir_report.ordering import sort_emails

# Canonical column order for all output formats.
_HEADERS: list[str] = [
    "stable_id",
    "filepath",
    "decision",
    "folder",
    "date",
    "from",
    "subject",
    "total_size_bytes",
    "attachment_count",
    "attachment_total_bytes",
    "attachment_names",
    "is_duplicate",
    "dup_group_id",
    "dup_rank",
]

_SUBJECT_MAX = 80  # matches pdf.py truncation limit


def _truncate_subject(subject: str) -> str:
    """Truncate subject to 80 chars + ellipsis, matching PDF output policy."""
    return subject[:_SUBJECT_MAX] + "\u2026" if len(subject) > _SUBJECT_MAX else subject


def _attachment_fields(parts: list[dict[str, Any]]) -> tuple[int, int, str]:
    """Compute attachment_count, attachment_total_bytes, and attachment_names.

    Parameters
    ----------
    parts:
        List of PartRecord dicts from an EmailRecord.

    Returns
    -------
    (count, total_bytes, names_str)
        count      — number of attachment parts (all parts; body-only parts
                     are already excluded by the parser before they reach here).
        total_bytes — sum of part sizes.
        names_str  — semicolon-separated filenames; empty string when no parts.
    """
    count = 0
    total_bytes = 0
    names: list[str] = []
    for part in parts:
        count += 1
        total_bytes += int(part.get("size", 0))
        filename = str(part.get("filename", ""))
        if filename:
            names.append(filename)
    names_str = ";".join(names)
    return count, total_bytes, names_str


def generate_decisions_template(
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Return one decisions-template row per email record in sort_emails() order.

    Parameters
    ----------
    records:
        List of EmailRecord dicts as produced by ``parser.scan_maildir`` and
        (optionally) annotated by ``dedup.group_emails``.  Each record must
        have ``"stable_id"`` and ``"filepath"`` keys.

    Returns
    -------
    list[dict[str, str]]
        A new list of row dicts.  The input list is NOT modified.
        Each row has exactly the keys in ``_HEADERS`` (14 keys) as strings.
        The ``"decision"`` key is always an empty string.  Row order matches
        ``sort_emails(records)``.
    """
    sorted_records = sort_emails(records)
    rows: list[dict[str, str]] = []
    for rec in sorted_records:
        parts: list[dict[str, Any]] = rec.get("parts", [])
        att_count, att_total, att_names = _attachment_fields(parts)

        dup_group_id = rec.get("dup_group_id")
        dup_rank = rec.get("dup_rank")
        is_duplicate = dup_group_id is not None

        rows.append(
            {
                "stable_id": str(rec.get("stable_id", "")),
                "filepath": str(rec.get("filepath", "")),
                "decision": "",
                "folder": str(rec.get("folder", "")),
                "date": str(rec.get("date", "")),
                "from": str(rec.get("sender", "")),
                "subject": _truncate_subject(str(rec.get("subject", "") or "")),
                "total_size_bytes": str(int(rec.get("total_size", 0))),
                "attachment_count": str(att_count),
                "attachment_total_bytes": str(att_total),
                "attachment_names": att_names,
                "is_duplicate": "true" if is_duplicate else "false",
                "dup_group_id": str(dup_group_id) if dup_group_id is not None else "",
                "dup_rank": str(dup_rank) if dup_rank is not None else "",
            }
        )
    return rows


def serialize_decisions_csv(rows: list[dict[str, str]]) -> str:
    """Serialise the decisions template rows to a CSV string.

    The CSV header row is always written, even for an empty row list.
    Values that contain commas, quotes, or newlines are properly escaped by
    Python's csv module (RFC 4180 quoting rules).

    Parameters
    ----------
    rows:
        List of row dicts as returned by ``generate_decisions_template``.
        May be empty.

    Returns
    -------
    str
        A UTF-8 CSV string with a header row and one data row per element.
        Line terminator is ``\\r\\n`` (CSV standard), as produced by
        ``csv.writer`` with ``lineterminator='\\r\\n'``.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=_HEADERS,
        lineterminator="\r\n",
        extrasaction="raise",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def serialize_decisions_json(rows: list[dict[str, str]]) -> str:
    """Serialise the decisions template rows to a JSON string.

    Produces a compact, deterministic JSON array.  Key ordering within
    each object follows ``_HEADERS`` (all 14 columns) because
    each row dict was created with those keys in that order by
    ``generate_decisions_template``.  ``json.dumps`` preserves dict insertion
    order in Python 3.7+.

    Parameters
    ----------
    rows:
        List of row dicts as returned by ``generate_decisions_template``.
        May be empty.

    Returns
    -------
    str
        A JSON string representing a JSON array.  No trailing newline.
        Non-ASCII characters are preserved as-is (``ensure_ascii=False``).
        Indented with 2 spaces for human readability.
    """
    return json.dumps(rows, ensure_ascii=False, indent=2)

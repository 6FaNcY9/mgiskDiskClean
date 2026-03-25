"""
ordering.py — Deterministic ordering rules for maildir_report.

All list outputs (emails, parts, duplicate groups) that appear in the PDF
or manifest MUST use functions from this module to ensure byte-for-byte
reproducible output independent of:
  - filesystem iteration order (``os.walk`` is not deterministic across systems)
  - dict insertion order
  - Python's default object identity hashing

Sort keys are documented explicitly for each function so the ordering contract
can be tested and reasoned about independently.

Anti-pattern reference (``scripts/maildir_viewer.py``)
-------------------------------------------------------
  The legacy script calls ``os.walk()`` without sorting ``dirs`` or ``files``,
  then assigns ``m["id"] = i`` based on walk position.  This makes both IDs
  and ordering non-deterministic across runs/systems.
"""

from __future__ import annotations

from typing import Any


def sort_emails(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *records* sorted deterministically.

    Sort key (primary → secondary):
    1. ``date``     — ISO-like string ``"YYYY-MM-DD HH:MM"``, lexicographic
                      sort is correct for this format.
    2. ``filepath`` — absolute path, lexicographic tiebreaker.  This is
                      unique within a single scan, so it fully resolves ties.

    The input list is NOT modified; a new list is returned.

    Parameters
    ----------
    records:
        List of email record dicts.  Each must have ``"date"`` and
        ``"filepath"`` keys; missing values are treated as empty strings.

    Returns
    -------
    list[dict[str, Any]]
        A new sorted list.  The original list is unchanged.
    """
    return sorted(
        records,
        key=lambda r: (r.get("date", ""), r.get("filepath", "")),
    )


def sort_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *parts* sorted deterministically.

    Sort key (primary → secondary):
    1. ``filename`` — decoded filename string, lexicographic.
    2. ``size``     — integer byte-count, ascending tiebreaker.

    The input list is NOT modified; a new list is returned.

    Parameters
    ----------
    parts:
        List of part record dicts.  Each should have ``"filename"`` and
        ``"size"`` keys; missing values are treated as empty string / 0.

    Returns
    -------
    list[dict[str, Any]]
        A new sorted list.  The original list is unchanged.
    """
    return sorted(
        parts,
        key=lambda p: (p.get("filename", ""), p.get("size", 0)),
    )


def sort_dup_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *groups* sorted deterministically.

    Sort key:
        The lexicographically smallest ``member_email_ids`` value within each
        group (i.e. ``min(group["member_email_ids"])``).  This is the
        "canonical member ID" of the group.  Because member IDs are SHA-256
        hex strings, this comparison is stable and well-defined.

    The input list is NOT modified; a new list is returned.

    Parameters
    ----------
    groups:
        List of duplicate group dicts.  Each must have a
        ``"member_email_ids"`` key containing a list of stable email IDs.

    Returns
    -------
    list[dict[str, Any]]
        A new sorted list.  The original list is unchanged.
    """

    def _canonical_key(group: dict[str, Any]) -> str:
        members = group.get("member_email_ids", [])
        if not members:
            return ""
        return min(members)  # lexicographically smallest stable ID

    return sorted(groups, key=_canonical_key)

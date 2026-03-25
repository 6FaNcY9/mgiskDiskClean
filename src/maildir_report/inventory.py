"""
inventory.py — Audited inventory reconciliation for maildir_report.

Design principles
-----------------
- NO silent differences: if "files on disk" ≠ "parsed record filepaths", raise
  InventoryMismatchError immediately with deterministic, sorted error lists.
- Deterministic normalisation: all paths are converted to absolute strings via
  os.path.abspath so that relative vs. absolute comparisons never cause false
  mismatches.
- Stable ordering in error output: missing and extra lists are always sorted
  lexicographically so that error messages are byte-for-byte reproducible
  across runs.
- Same scope as scan_maildir: only cur/ and new/ sub-directories, hidden files
  (starting with '.') excluded, tmp/ excluded.

Usage
-----
    from maildir_report.inventory import list_maildir_files, reconcile_inventory

    disk_files = list_maildir_files(root)          # sorted absolute paths
    records = scan_maildir(root)                   # may raise MailParseError
    reconcile_inventory(root, records)             # raises InventoryMismatchError on mismatch
"""

from __future__ import annotations

import os
from typing import Any


# ── typed exception ────────────────────────────────────────────────────────────


class InventoryMismatchError(Exception):
    """Raised when the set of files on disk differs from parsed record filepaths.

    Attributes
    ----------
    missing : list[str]
        Sorted absolute paths that appear in parsed records but are NOT found
        on disk.  These are "phantom" entries — the record was created from a
        file that is no longer (or was never) present.
    extra : list[str]
        Sorted absolute paths that exist on disk but have NO corresponding
        record.  These are files that were not parsed — a completeness gap.
    """

    def __init__(self, missing: list[str], extra: list[str]) -> None:
        self.missing: list[str] = sorted(missing)
        self.extra: list[str] = sorted(extra)
        parts: list[str] = []
        if self.missing:
            parts.append(
                f"missing from disk ({len(self.missing)}): " + ", ".join(self.missing)
            )
        if self.extra:
            parts.append(
                f"extra on disk, not in records ({len(self.extra)}): "
                + ", ".join(self.extra)
            )
        summary = "; ".join(parts) if parts else "unknown mismatch"
        super().__init__(f"Inventory mismatch — {summary}")


# ── disk listing ───────────────────────────────────────────────────────────────


def list_maildir_files(root: str) -> list[str]:
    """Return a sorted list of absolute file paths for all non-hidden Maildir messages.

    Mirrors the scanning scope of ``parser.scan_maildir()``:
    - Only ``cur/`` and ``new/`` sub-directories are included.
    - ``tmp/`` is always excluded.
    - Files whose names start with ``'.'`` are skipped (Maildir hidden-file convention).
    - Results are sorted lexicographically for deterministic ordering.

    Parameters
    ----------
    root:
        Path to the Maildir root directory.

    Returns
    -------
    list[str]
        Sorted list of absolute path strings.  Empty list when no eligible files
        exist.
    """
    result: list[str] = []
    for dirpath, dirs, files in os.walk(root):
        # Sort dirs in-place so walk descends deterministically; exclude tmp/.
        dirs[:] = sorted(d for d in dirs if d != "tmp")

        base = os.path.basename(dirpath)
        if base not in ("cur", "new"):
            continue

        for filename in sorted(files):
            if filename.startswith("."):
                continue  # skip hidden/lock files (Maildir convention)
            fpath = os.path.abspath(os.path.join(dirpath, filename))
            result.append(fpath)

    return sorted(result)


# ── reconciliation ─────────────────────────────────────────────────────────────


def reconcile_inventory(root: str, records: list[dict[str, Any]]) -> None:
    """Assert that disk files and parsed record filepaths are in perfect agreement.

    Compares the set of files returned by ``list_maildir_files(root)`` with the
    set of ``filepath`` values extracted from *records*.  Both sets are normalised
    to absolute paths before comparison.

    If the sets match exactly this function returns ``None`` silently.

    If there is any mismatch, raises ``InventoryMismatchError`` immediately with:
    - ``missing`` — paths in *records* but NOT on disk (phantom/stale entries).
    - ``extra``   — paths on disk but NOT in *records* (unscanned files).

    Parameters
    ----------
    root:
        Path to the Maildir root directory.
    records:
        List of EmailRecord dicts (or any dicts with a ``"filepath"`` key).

    Returns
    -------
    None
        Always ``None`` on success.

    Raises
    ------
    InventoryMismatchError
        When ``disk_set != record_set``, with deterministic sorted path lists.
    """
    disk_set: set[str] = set(list_maildir_files(root))

    # Normalise record filepaths to absolute paths for fair comparison.
    record_set: set[str] = {
        os.path.abspath(str(r["filepath"])) for r in records if r.get("filepath")
    }

    missing = sorted(record_set - disk_set)  # in records but not on disk
    extra = sorted(disk_set - record_set)  # on disk but not in records

    if missing or extra:
        raise InventoryMismatchError(missing=missing, extra=extra)

    return None

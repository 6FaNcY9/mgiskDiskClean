"""
walk.py — Deterministic Maildir filesystem traversal + folder naming.

Design principles
-----------------
- NO non-determinism: both dirs and files are sorted at every os.walk level.
- Sorting is applied in-place on dirs so os.walk descends in stable order.
- Only ``cur/`` and ``new/`` sub-directories are yielded; ``tmp/`` is always
  excluded (hidden by the dirs[:] filter before os.walk descends).
- Maildir++ dot-folder naming is normalized to human-readable labels via
  ``normalize_folder_name()``.  The contract is:
    - Empty string, ".", "cur", "new"  → "INBOX"
    - ".Sent"                          → "Sent"    (strip leading dot)
    - ".INBOX.Work"                    → "INBOX/Work" (dot as path separator)
    - ".Sent.Archive"                  → "Sent/Archive"
    - "Archive" (no leading dot)       → "Archive" (returned unchanged)

Public API
----------
``normalize_folder_name(raw: str) -> str``
    Convert a raw Maildir++ folder directory name to a display label.

``deterministic_walk(root: str) -> Iterator[tuple[str, str]]``
    Yield ``(filepath, folder_name)`` tuples for every mail file under
    *root*, in a stable order that does not depend on filesystem iteration
    order.

Anti-pattern reference
----------------------
  WRONG (maildir_viewer.py:148): os.walk(root) without sorting dirs/files
  WRONG (scripts/*):             folder = parts[0] without normalization
  CORRECT (this module):         dirs[:] = sorted(…); files sorted per dir
"""

from __future__ import annotations

import os
from typing import Iterator


# ── folder naming normalization ────────────────────────────────────────────────


def normalize_folder_name(raw: str) -> str:
    """Convert a raw Maildir++ folder directory name to a human-readable label.

    Maildir++ stores sub-folders as top-level directories whose names start
    with a dot.  The dot is a namespace separator: each additional dot within
    the name represents one level of hierarchy.

    Examples
    --------
    >>> normalize_folder_name("cur")
    'INBOX'
    >>> normalize_folder_name("new")
    'INBOX'
    >>> normalize_folder_name(".")
    'INBOX'
    >>> normalize_folder_name("")
    'INBOX'
    >>> normalize_folder_name(".Sent")
    'Sent'
    >>> normalize_folder_name(".Trash")
    'Trash'
    >>> normalize_folder_name(".INBOX.Work")
    'INBOX/Work'
    >>> normalize_folder_name(".Sent.Archive")
    'Sent/Archive'
    >>> normalize_folder_name(".A.B.C")
    'A/B/C'
    >>> normalize_folder_name("Archive")
    'Archive'

    Parameters
    ----------
    raw:
        The raw directory name component (e.g. ``".Sent"``, ``"cur"``, ``"."``).

    Returns
    -------
    str
        A normalized, human-readable folder label.
    """
    # Empty string, root-Maildir sentinel, and Maildir internal dirs → INBOX
    if not raw or raw in {".", "cur", "new"}:
        return "INBOX"

    # Maildir++ sub-folders start with a leading dot.  The additional dots
    # within the name are path-level separators (IMAP-style hierarchy).
    if raw.startswith("."):
        # Strip the leading dot, then split remaining by dot for hierarchy.
        without_leader = raw[1:]
        if not without_leader:
            # bare "." → INBOX
            return "INBOX"
        parts = without_leader.split(".")
        return "/".join(parts)

    # Plain name with no leading dot (non-standard, but accepted as-is).
    return raw


# ── deterministic Maildir traversal ───────────────────────────────────────────


def deterministic_walk(root: str) -> Iterator[tuple[str, str]]:
    """Yield ``(filepath, folder_name)`` for every mail file under *root*.

    Traversal is fully deterministic:
    - ``dirs`` is sorted in-place at every level so ``os.walk`` descends
      sub-directories in lexicographic order.
    - ``files`` is sorted before iteration at every directory level.
    - ``tmp`` directories are excluded before descent (never visited).
    - Files starting with ``.`` are skipped (Maildir lock/hidden file
      convention).

    Only ``cur/`` and ``new/`` leaves are yielded.  All other directories
    (e.g. the root itself, ``tmp/``, Maildir++ dot-folder roots) are
    traversed for navigation but do not yield mail files directly.

    Parameters
    ----------
    root:
        Absolute or relative path to the Maildir root directory.

    Yields
    ------
    tuple[str, str]
        ``(filepath, folder_name)`` where:
        - *filepath* is the absolute path to the mail file as a ``str``.
        - *folder_name* is the normalized folder label (e.g. ``"INBOX"``,
          ``"Sent"``, ``"INBOX/Archive"``).
    """
    abs_root = os.path.abspath(root)

    for dirpath, dirs, files in os.walk(abs_root):
        # ── 1. Sort and prune dirs so os.walk descends deterministically ─────
        # Remove 'tmp' before sorting; os.walk will not descend into it.
        dirs[:] = sorted(d for d in dirs if d != "tmp")

        # ── 2. Only yield files when we are inside a cur/ or new/ leaf ───────
        base = os.path.basename(dirpath)
        if base not in ("cur", "new"):
            continue

        # ── 3. Determine the normalized folder label ──────────────────────────
        # The folder label comes from the *parent* directory of cur/new.
        # For root-level INBOX:  abs_root/cur → parent == abs_root → "INBOX"
        # For sub-folder .Sent:  abs_root/.Sent/cur → parent == abs_root/.Sent
        #                         → relative segment == ".Sent" → "Sent"
        parent = os.path.dirname(dirpath)
        if os.path.abspath(parent) == abs_root:
            # Directly under root — this is INBOX cur/ or new/
            folder_name = "INBOX"
        else:
            # Maildir++ sub-folder: get the name of the immediate parent dir.
            raw_folder = os.path.basename(parent)
            folder_name = normalize_folder_name(raw_folder)

        # ── 4. Yield files in sorted order, skipping hidden/lock files ────────
        for filename in sorted(files):
            if filename.startswith("."):
                continue  # skip Maildir lock files and hidden files
            fpath = os.path.join(dirpath, filename)
            yield fpath, folder_name

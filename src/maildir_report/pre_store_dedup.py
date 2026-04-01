"""
pre_store_dedup.py — Local pre-store email dedup for maildir_report.

Purpose
-------
After ``store-mailbox`` has rsynced a remote mailbox to the local data copy,
this module identifies duplicate email *files* (same RFC-2822 content) within
the local Maildir and quarantines the non-canonical copies.

Scope guardrail
---------------
Operates exclusively on the local stored Maildir path passed via
``--maildir-root``.  The remote server mailbox is NEVER touched.

Duplicate criterion
-------------------
Two email files are duplicates when they have the same **content hash**
(SHA-256 of raw file bytes).  This is an email-file identity check — not
attachment-content dedup (that belongs to Task 2b).

Determinism guarantees
----------------------
- Candidate sets are sorted by ``(date_str, filepath)`` (same sort key as
  ``ordering.sort_emails``).  The canonical copy is always the
  **first element** after this sort (i.e. the oldest file; filepath is the
  tiebreaker ensuring uniqueness).
- ``candidate_set_hash`` is the SHA-256 of the newline-joined sorted filepaths
  within the candidate set (stable across runs).
- Audit log lines are written in the same deterministic order.

Default behaviour
-----------------
Quarantine-only (non-destructive): duplicate files are **moved** to
``<quarantine_root>/<candidate_set_hash_prefix>/<filename>`` and an audit log
entry is appended to ``<quarantine_root>/audit.log``.

No files are deleted.  The canonical copy is always left in place.

Public API
----------
run_pre_store_dedup(maildir_root, quarantine_root, dry_run=False)
    Scan *maildir_root*, identify file-level duplicates, quarantine non-
    canonical copies, and return a ``DedupResult`` dataclass.

main(argv=None) -> int
    CLI entrypoint.  ``python -m maildir_report.pre_store_dedup --help``.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import pathlib
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

from maildir_report.walk import deterministic_walk


# ── result dataclass ──────────────────────────────────────────────────────────


@dataclass
class CandidateSet:
    """One group of files with identical raw-bytes content hash.

    Attributes
    ----------
    content_hash:
        SHA-256 hex of the raw file bytes (64 chars).
    candidate_set_hash:
        SHA-256 of the newline-joined sorted filepaths in this candidate set.
        Stable across repeated runs on the same files.
    canonical_path:
        Absolute path of the file kept in place (first in sort order).
    duplicate_paths:
        Absolute paths of the non-canonical copies (to be quarantined).
    """

    content_hash: str
    candidate_set_hash: str
    canonical_path: str
    duplicate_paths: list[str] = field(default_factory=list)


@dataclass
class DedupResult:
    """Summary returned by ``run_pre_store_dedup``.

    Attributes
    ----------
    candidate_sets:
        List of ``CandidateSet`` objects (one per group of identical files).
        Empty when no duplicates were found.
    quarantined_paths:
        Absolute paths actually moved to quarantine (empty in dry-run mode).
    audit_log_path:
        Absolute path of the audit log file, or ``None`` when no log was
        written (e.g. no duplicates found in non-dry-run mode).
    dry_run:
        ``True`` when the run was in dry-run mode (no files moved).
    """

    candidate_sets: list[CandidateSet] = field(default_factory=list)
    quarantined_paths: list[str] = field(default_factory=list)
    audit_log_path: str | None = None
    dry_run: bool = False


# ── internal helpers ──────────────────────────────────────────────────────────


def _sha256_file(path: str) -> str:
    """Return lowercase hex SHA-256 of the raw bytes of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _candidate_set_hash(sorted_filepaths: list[str]) -> str:
    """Return a stable SHA-256 for a candidate set.

    Computed from the newline-joined *sorted* absolute filepaths, so the
    hash is invariant to the order in which the paths were discovered.

    Parameters
    ----------
    sorted_filepaths:
        List of absolute filepaths already sorted deterministically.

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest.
    """
    payload = "\n".join(sorted_filepaths)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sort_key(path: str) -> tuple[str, str]:
    """Return the deterministic sort key ``(mtime_str, filepath)``.

    We use the last-modified time of the file as a proxy for email date when
    the file itself hasn't been parsed (this avoids a full parse inside dedup).
    For files within the same second, filepath breaks ties deterministically.

    Note: the ``date`` field from the parsed email record would be ideal, but
    this module operates at the filesystem level before the full parse pipeline
    runs.  Using mtime is consistent and deterministic within a single stored
    mailbox copy.
    """
    try:
        mtime = os.path.getmtime(path)
        # Format as ISO-like string so lexicographic sort is chronological.
        mtime_str = datetime.datetime.fromtimestamp(
            mtime, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S")
    except OSError:
        mtime_str = ""
    return (mtime_str, path)


def _build_candidate_sets(maildir_root: str) -> list[CandidateSet]:
    """Scan *maildir_root* and return one ``CandidateSet`` per duplicate group.

    A candidate set exists only when ≥2 files share the same raw-bytes
    SHA-256.  Single-file groups are never returned.

    The canonical copy within each set is the **first** file after sorting by
    ``_sort_key`` (oldest mtime, with filepath tiebreaker).  This ordering is
    deterministic and stable across repeated calls on the same directory tree.

    Parameters
    ----------
    maildir_root:
        Absolute or relative path to the Maildir root directory.

    Returns
    -------
    list[CandidateSet]
        Sorted by ``candidate_set_hash`` for deterministic iteration order.
    """
    # ── 1. Collect all mail file paths from the Maildir ───────────────────────
    all_paths: list[str] = [
        filepath for filepath, _folder in deterministic_walk(maildir_root)
    ]

    if not all_paths:
        return []

    # ── 2. Group paths by content hash ────────────────────────────────────────
    hash_to_paths: dict[str, list[str]] = {}
    for path in all_paths:
        try:
            h = _sha256_file(path)
        except OSError:
            continue  # skip unreadable files gracefully
        hash_to_paths.setdefault(h, []).append(path)

    # ── 3. Keep only groups with ≥2 members (actual duplicates) ──────────────
    candidate_sets: list[CandidateSet] = []
    for content_hash, paths in hash_to_paths.items():
        if len(paths) < 2:
            continue

        # Deterministic ordering: sort by (mtime_str, filepath)
        sorted_paths = sorted(paths, key=_sort_key)

        # Stable candidate_set_hash from the sorted filepath list
        csh = _candidate_set_hash(sorted_paths)

        cs = CandidateSet(
            content_hash=content_hash,
            candidate_set_hash=csh,
            canonical_path=sorted_paths[0],
            duplicate_paths=sorted_paths[1:],
        )
        candidate_sets.append(cs)

    # ── 4. Sort candidate sets by candidate_set_hash for stable iteration ─────
    candidate_sets.sort(key=lambda cs: cs.candidate_set_hash)
    return candidate_sets


def _quarantine_path(quarantine_root: str, cs: CandidateSet, dup_path: str) -> str:
    """Return the destination path for a duplicate file inside *quarantine_root*.

    Layout: ``<quarantine_root>/<csh_prefix8>/<original_filename>``

    The first 8 hex chars of ``candidate_set_hash`` form a sub-directory that
    groups all quarantined copies from the same candidate set.  This keeps the
    quarantine navigable while preserving the association between quarantined
    files and their candidate set.

    Collisions are avoided by appending a suffix derived from the content hash
    when two files with different names happen to land in the same sub-dir.
    """
    subdir = cs.candidate_set_hash[:8]
    filename = os.path.basename(dup_path)
    dest_dir = os.path.join(quarantine_root, subdir)
    dest_path = os.path.join(dest_dir, filename)

    # If a file with the same name already exists in quarantine (e.g. from a
    # prior partial run), append the content hash prefix to avoid overwriting.
    if os.path.exists(dest_path):
        name, ext = os.path.splitext(filename)
        dest_path = os.path.join(dest_dir, f"{name}.{cs.content_hash[:8]}{ext}")

    return dest_path


def _format_audit_entry(
    cs: CandidateSet,
    quarantined: list[tuple[str, str]],
    timestamp: str,
    dry_run: bool,
) -> str:
    """Format one audit log entry for a candidate set.

    Parameters
    ----------
    cs:
        The ``CandidateSet`` being processed.
    quarantined:
        List of ``(src_path, dst_path)`` tuples for moved files.
    timestamp:
        ISO 8601 UTC timestamp string.
    dry_run:
        Whether this was a dry run.

    Returns
    -------
    str
        Multi-line audit log entry (trailing newline included).
    """
    action = "DRY-RUN" if dry_run else "QUARANTINED"
    lines = [
        f"--- {timestamp} ---",
        f"content_hash       : {cs.content_hash}",
        f"candidate_set_hash : {cs.candidate_set_hash}",
        f"canonical_kept     : {cs.canonical_path}",
        f"duplicate_count    : {len(cs.duplicate_paths)}",
    ]
    for src, dst in quarantined:
        lines.append(f"  {action}: {src} -> {dst}")
    lines.append("")
    return "\n".join(lines)


# ── public API ────────────────────────────────────────────────────────────────


def run_pre_store_dedup(
    maildir_root: str,
    quarantine_root: str,
    dry_run: bool = False,
) -> DedupResult:
    """Scan *maildir_root*, identify file-level duplicates, and quarantine them.

    Parameters
    ----------
    maildir_root:
        Absolute or relative path to the local stored Maildir root.
        Must contain ``cur/`` and/or ``new/`` sub-directories.
        **Only this path is ever modified** (by moving files out).
    quarantine_root:
        Absolute or relative path where duplicate files will be moved.
        Created if it does not exist.  An ``audit.log`` file is appended
        inside this directory.
    dry_run:
        When ``True``, no files are moved.  The audit log entry is written
        with ``DRY-RUN`` markers.  Useful for inspecting what would happen.

    Returns
    -------
    DedupResult
        Summary of all candidate sets found and files quarantined.
    """
    maildir_root = os.path.abspath(maildir_root)
    quarantine_root = os.path.abspath(quarantine_root)

    candidate_sets = _build_candidate_sets(maildir_root)

    if not candidate_sets:
        return DedupResult(candidate_sets=[], dry_run=dry_run)

    # Ensure quarantine directory exists (even in dry-run so log can be written)
    os.makedirs(quarantine_root, exist_ok=True)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    all_quarantined: list[str] = []
    audit_entries: list[str] = []
    audit_log_path = os.path.join(quarantine_root, "audit.log")

    for cs in candidate_sets:
        moved_pairs: list[tuple[str, str]] = []

        for dup_path in cs.duplicate_paths:
            dest = _quarantine_path(quarantine_root, cs, dup_path)

            if not dry_run:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(dup_path, dest)
                all_quarantined.append(dest)

            moved_pairs.append((dup_path, dest))

        audit_entries.append(_format_audit_entry(cs, moved_pairs, timestamp, dry_run))

    # Append audit log (always, even for dry-run, so results are inspectable)
    with open(audit_log_path, "a", encoding="utf-8") as fh:
        for entry in audit_entries:
            fh.write(entry + "\n")

    return DedupResult(
        candidate_sets=candidate_sets,
        quarantined_paths=all_quarantined,
        audit_log_path=audit_log_path,
        dry_run=dry_run,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maildir-pre-store-dedup",
        description=(
            "Local pre-store email dedup for maildir_report.\n\n"
            "Scans a local stored Maildir directory for email files with\n"
            "identical raw content (SHA-256), then quarantines non-canonical\n"
            "copies into a quarantine directory.\n\n"
            "Default behaviour is QUARANTINE-ONLY (non-destructive):\n"
            "  - The canonical copy (oldest by mtime, filepath tiebreaker)\n"
            "    is kept in place.\n"
            "  - Duplicates are MOVED (not deleted) to <quarantine-root>/.\n"
            "  - An audit.log is appended in <quarantine-root>/.\n\n"
            "Scope guardrail: only the local --maildir-root path is touched.\n"
            "The remote server mailbox is NEVER accessed by this command."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--maildir-root",
        required=True,
        metavar="PATH",
        help=(
            "Path to the local stored Maildir root directory "
            "(e.g. $DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir). "
            "Must contain cur/ and/or new/ sub-directories."
        ),
    )
    parser.add_argument(
        "--quarantine-root",
        required=True,
        metavar="PATH",
        help=(
            "Directory where duplicate files are moved. "
            "Created if it does not exist. "
            "An audit.log is appended here with every run."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Scan and report what would be quarantined, but do not move any "
            "files.  The audit.log is still written with DRY-RUN markers."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run pre-store dedup.

    Parameters
    ----------
    argv:
        Argument list.  Defaults to ``sys.argv[1:]`` when ``None``.

    Returns
    -------
    int
        ``0`` on success (including the case where no duplicates are found).
        ``1`` on any error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = run_pre_store_dedup(
            maildir_root=args.maildir_root,
            quarantine_root=args.quarantine_root,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # ── summary output ────────────────────────────────────────────────────────
    if not result.candidate_sets:
        print("pre-store-dedup: no duplicate email files found.")
        return 0

    total_dup_files = sum(len(cs.duplicate_paths) for cs in result.candidate_sets)
    mode = "DRY-RUN" if result.dry_run else "QUARANTINED"
    print(
        f"pre-store-dedup: {len(result.candidate_sets)} duplicate group(s) found, "
        f"{total_dup_files} file(s) {mode}."
    )
    for cs in result.candidate_sets:
        print(
            f"  group {cs.candidate_set_hash[:16]}... "
            f"canonical={os.path.basename(cs.canonical_path)} "
            f"duplicates={len(cs.duplicate_paths)}"
        )
    if result.audit_log_path:
        print(f"  audit log: {result.audit_log_path}")

    return 0


# ── module entrypoint (python -m maildir_report.pre_store_dedup) ──────────────

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

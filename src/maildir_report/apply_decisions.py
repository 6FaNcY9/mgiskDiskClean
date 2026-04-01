"""
apply_decisions.py — Local CLI tool to apply reviewed decisions to a Maildir.

Subcommands
-----------
plan     Read decisions.reviewed.csv, find mail files, write cleanup_plan.json.
apply    Read cleanup_plan.json, quarantine files, write audit.jsonl.
restore  Move quarantined files back to original paths using audit.jsonl.
purge    Delete quarantined files (requires 7-day retention check by default).

Global flags
------------
--dry-run       Print what would happen but do NOT move/delete files or write
                persistent state.
--break-glass   Lift the 10,000-file cap (dangerous; use with care).

Stable-ID strategy
------------------
The decisions CSV (produced by decisions_template.py) carries BOTH
  stable_id   — SHA-256(filepath + \\x00 + message_id)
  filepath    — absolute path to the Maildir file as recorded at scan time.
Matching is therefore straightforward: use ``filepath`` from the CSV row
directly (it is already the canonical disk path).  After resolving symlinks
via os.path.realpath() we verify the resolved path starts with realpath(maildir_root).
This is the primary safety gate against path traversal.

Usage
-----
    python -m maildir_report.apply_decisions plan \\
        --maildir-root /mail/boxes/alice \\
        --decisions-csv decisions.reviewed.csv

    python -m maildir_report.apply_decisions apply \\
        --plan cleanup_plan.json \\
        --confirm abcdef12

    python -m maildir_report.apply_decisions restore \\
        --plan-id <uuid> \\
        --maildir-root /mail/boxes/alice

    python -m maildir_report.apply_decisions purge \\
        --plan-id <uuid> \\
        --maildir-root /mail/boxes/alice \\
        --confirm

    python -m maildir_report.apply_decisions --help
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── constants ──────────────────────────────────────────────────────────────────

_FILE_CAP = 10_000  # max candidates without --break-glass
_QUARANTINE_DIR = ".quarantine"
_AUDIT_FILENAME = "audit.jsonl"
_PLAN_TTL_HOURS = 24  # cleanup_plan.json expires after 24 h
_PURGE_RETENTION_DAYS = 7  # quarantine files must be this old before purge
_CONFIRM_MIN_LEN = 8  # minimum prefix length for --confirm


# ── helpers ────────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    """Return an aware UTC datetime (tz-aware so timedelta arithmetic is safe)."""
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    """Return an ISO-8601 string without microseconds."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 UTC string produced by _iso()."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _safe_realpath(path: str) -> str:
    """Return the canonical (real) absolute path."""
    return os.path.realpath(os.path.abspath(path))

class _TraversalError(ValueError):
    """Raised when a resolved path is outside the allowed maildir_root."""


def _assert_under_root(filepath: str, root_real: str) -> None:
    """Raise _TraversalError if *filepath* (realpath) is not under *root_real*.

    Guards against path-traversal attacks where a tampered plan file tries to
    reference files outside the declared maildir_root.
    """
    real = _safe_realpath(filepath)
    # Must start with root_real + os.sep OR be exactly root_real.
    if real != root_real and not real.startswith(root_real + os.sep):
        raise _TraversalError(
            f"Path traversal detected: {filepath!r} is not under {root_real!r}"
        )


def _candidate_set_hash(stable_ids: list[str]) -> str:
    """SHA-256 of sorted stable_ids joined by newlines."""
    joined = "\n".join(sorted(stable_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _read_decisions_csv(csv_path: str) -> list[dict[str, str]]:
    """Return all rows from the decisions CSV as dicts."""
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def _quarantine_base(maildir_root: str, plan_id: str) -> str:
    """Return the quarantine directory for a given plan."""
    return os.path.join(maildir_root, _QUARANTINE_DIR, plan_id)


def _quarantine_path_for(
    maildir_root: str,
    plan_id: str,
    original_filepath: str,
    maildir_root_real: str,
) -> str:
    """Compute the quarantine destination for a single file.

    The structure mirrors the original sub-directory (cur/ or new/) so that
    the filename is unambiguous.

    Example:
        /mail/alice/cur/1234.some:2,S  →  /mail/alice/.quarantine/<plan_id>/cur/1234.some:2,S
    """
    # Find the sub-path relative to maildir_root.
    real_orig = _safe_realpath(original_filepath)
    # Strip the root prefix (we already validated it's under root).
    rel = os.path.relpath(real_orig, maildir_root_real)
    return os.path.join(_quarantine_base(maildir_root, plan_id), rel)


# ── plan subcommand ────────────────────────────────────────────────────────────


def cmd_plan(args: argparse.Namespace) -> int:
    """Build a cleanup plan from a reviewed decisions CSV."""
    maildir_root = os.path.abspath(args.maildir_root)
    maildir_root_real = _safe_realpath(maildir_root)
    decisions_csv = args.decisions_csv
    mode = args.mode
    dry_run: bool = args.dry_run
    break_glass: bool = args.break_glass
    output_plan: str = getattr(args, "output_plan", None) or "cleanup_plan.json"

    # Read CSV
    try:
        rows = _read_decisions_csv(decisions_csv)
    except OSError as exc:
        print(f"[ERROR] Cannot read decisions CSV: {exc}", file=sys.stderr)
        return 1

    # Filter rows where decision == "delete" (case-insensitive)
    delete_rows = [r for r in rows if r.get("decision", "").strip().lower() == "delete"]

    # Resolve candidates
    candidates: list[dict[str, Any]] = []
    skipped = 0
    for row in delete_rows:
        filepath = row.get("filepath", "").strip()
        stable_id = row.get("stable_id", "").strip()
        if not filepath:
            print(
                f"[WARN] Row with stable_id={stable_id!r} has no filepath — skipping.",
                file=sys.stderr,
            )
            skipped += 1
            continue

        # Path traversal / allowlist check
        real = _safe_realpath(filepath)
        if real != maildir_root_real and not real.startswith(
            maildir_root_real + os.sep
        ):
            print(
                f"[WARN] filepath {filepath!r} is not under maildir_root — skipping.",
                file=sys.stderr,
            )
            skipped += 1
            continue

        # File must exist
        if not os.path.isfile(filepath):
            print(
                f"[WARN] filepath {filepath!r} does not exist — skipping.",
                file=sys.stderr,
            )
            skipped += 1
            continue

        try:
            size_bytes = os.path.getsize(filepath)
        except OSError:
            size_bytes = 0

        candidates.append(
            {
                "stable_id": stable_id,
                "filepath": filepath,
                "size_bytes": size_bytes,
            }
        )

    # Sort by stable_id for deterministic ordering
    candidates.sort(key=lambda c: c["stable_id"])

    # Cap check
    if len(candidates) > _FILE_CAP and not break_glass:
        print(
            f"[ERROR] {len(candidates)} candidates exceed the {_FILE_CAP}-file cap. "
            "Use --break-glass to override.",
            file=sys.stderr,
        )
        return 1

    # Compute hash
    stable_ids = [c["stable_id"] for c in candidates]
    candidate_hash = _candidate_set_hash(stable_ids)

    # Summary
    total_bytes = sum(c["size_bytes"] for c in candidates)
    print(
        f"[plan] Found {len(candidates)} candidate(s) ({total_bytes:,} bytes total). "
        f"Skipped {skipped}. mode={mode}"
    )
    print(f"[plan] candidate_set_hash = {candidate_hash}")

    now = _utcnow()
    plan = {
        "plan_id": str(uuid.uuid4()),
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(hours=_PLAN_TTL_HOURS)),
        "maildir_root": maildir_root,
        "mode": mode,
        "candidate_set_hash": candidate_hash,
        "candidates": candidates,
    }

    with open(output_plan, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    if dry_run:
        print(f"[plan --dry-run] Written to {output_plan} (preview only; run `apply` to execute)")
    else:
        print(f"[plan] Written to {output_plan}")
    return 0


# ── apply subcommand ───────────────────────────────────────────────────────────


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply a cleanup plan: move files to quarantine and write audit.jsonl."""
    plan_path: str = args.plan
    confirm: str = args.confirm
    dry_run: bool = args.dry_run
    break_glass: bool = args.break_glass

    # Load plan
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan: dict[str, Any] = json.load(fh)
    except OSError as exc:
        print(f"[ERROR] Cannot read plan file: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Invalid plan JSON: {exc}", file=sys.stderr)
        return 1

    plan_id: str = plan["plan_id"]
    maildir_root: str = plan["maildir_root"]
    maildir_root_real = _safe_realpath(maildir_root)
    candidate_hash: str = plan["candidate_set_hash"]
    candidates: list[dict[str, Any]] = plan.get("candidates", [])
    mode: str = plan.get("mode", "quarantine")

    # Expiry check
    try:
        expires_at = _parse_iso(plan["expires_at"])
    except (KeyError, ValueError):
        print("[ERROR] Plan has no valid expires_at.", file=sys.stderr)
        return 1
    if _utcnow() > expires_at:
        print(
            f"[ERROR] Plan expired at {plan['expires_at']}. Re-run `plan` to create a fresh plan.",
            file=sys.stderr,
        )
        return 1

    # Confirm prefix check (8+ chars required)
    confirm_clean = confirm.strip()
    if len(confirm_clean) < _CONFIRM_MIN_LEN:
        print(
            f"[ERROR] --confirm must be at least {_CONFIRM_MIN_LEN} characters.",
            file=sys.stderr,
        )
        return 1
    if not candidate_hash.startswith(confirm_clean):
        print(
            f"[ERROR] --confirm {confirm_clean!r} does not match candidate_set_hash prefix.",
            file=sys.stderr,
        )
        return 1

    # Cap check
    if len(candidates) > _FILE_CAP and not break_glass:
        print(
            f"[ERROR] Plan has {len(candidates)} candidates which exceeds the {_FILE_CAP}-file cap. "
            "Use --break-glass to override.",
            file=sys.stderr,
        )
        return 1

    # Validate all filepaths are under maildir_root (path traversal guard)
    for c in candidates:
        try:
            _assert_under_root(c["filepath"], maildir_root_real)
        except _TraversalError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

    audit_lines: list[str] = []
    moved = 0
    skipped = 0

    for c in candidates:
        src: str = c["filepath"]
        stable_id: str = c["stable_id"]

        dest = _quarantine_path_for(maildir_root, plan_id, src, maildir_root_real)

        if dry_run:
            if mode == "delete":
                print(f"[apply --dry-run] Would delete: {src!r}")
            else:
                print(f"[apply --dry-run] Would move: {src!r} \u2192 {dest!r}")
            continue

        if mode == "delete":
            # delete mode: remove file permanently (no quarantine)
            # Idempotency: if already gone, skip
            if not os.path.isfile(src):
                print(f"[apply] Already deleted or not found, skipping: {src!r}")
                skipped += 1
                now_str = _iso(_utcnow())
                audit_lines.append(
                    json.dumps(
                        {
                            "stable_id": stable_id,
                            "original_path": src,
                            "action": "delete",
                            "deleted_at": now_str,
                            "note": "already_deleted",
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            os.remove(src)
            moved += 1
            now_str = _iso(_utcnow())
            audit_lines.append(
                json.dumps(
                    {
                        "stable_id": stable_id,
                        "original_path": src,
                        "action": "delete",
                        "deleted_at": now_str,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            # quarantine mode: move file to .quarantine/<plan_id>/
            # Idempotency: if already at dest, skip
            if os.path.exists(dest) and not os.path.exists(src):
                print(f"[apply] Already quarantined (skipping): {dest!r}")
                skipped += 1
                now_str = _iso(_utcnow())
                audit_lines.append(
                    json.dumps(
                        {
                            "stable_id": stable_id,
                            "original_path": src,
                            "quarantine_path": dest,
                            "moved_at": now_str,
                            "note": "already_quarantined",
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            if not os.path.isfile(src):
                print(f"[apply] Source not found, skipping: {src!r}", file=sys.stderr)
                skipped += 1
                continue

            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            moved += 1
            now_str = _iso(_utcnow())
            audit_lines.append(
                json.dumps(
                    {
                        "stable_id": stable_id,
                        "original_path": src,
                        "quarantine_path": dest,
                        "moved_at": now_str,
                    },
                    ensure_ascii=False,
                )
            )

    if not dry_run:
        if mode == "delete":
            audit_path = os.path.join(maildir_root, ".cleanup_log", plan_id, _AUDIT_FILENAME)
        else:
            audit_path = os.path.join(_quarantine_base(maildir_root, plan_id), _AUDIT_FILENAME)
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(audit_lines))
            if audit_lines:
                fh.write("\n")
        print(f"[apply] Done. Moved: {moved}, Skipped: {skipped}. Audit: {audit_path}")
    else:
        print(
            f"[apply --dry-run] Would process {len(candidates)} file(s) (mode={mode}). No changes made."
        )
    return 0

# ── restore subcommand ─────────────────────────────────────────────────────────


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore quarantined files to their original paths."""
    plan_id: str = args.plan_id
    maildir_root: str = os.path.abspath(args.maildir_root)
    maildir_root_real = _safe_realpath(maildir_root)
    dry_run: bool = args.dry_run

    audit_path = os.path.join(_quarantine_base(maildir_root, plan_id), _AUDIT_FILENAME)
    if not os.path.isfile(audit_path):
        print(f"[ERROR] Audit file not found: {audit_path}", file=sys.stderr)
        return 1

    entries = _read_audit(audit_path)
    restored = 0
    skipped = 0

    for entry in entries:
        src = entry.get("quarantine_path", "")
        dest = entry.get("original_path", "")

        # Path traversal guard on original_path
        try:
            _assert_under_root(dest, maildir_root_real)
        except _TraversalError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

        if dry_run:
            print(f"[restore --dry-run] Would restore: {src!r} → {dest!r}")
            continue

        if not os.path.isfile(src):
            print(
                f"[restore] Quarantine file not found, skipping: {src!r}",
                file=sys.stderr,
            )
            skipped += 1
            continue

        if os.path.exists(dest):
            print(
                f"[restore] Original path already exists, skipping: {dest!r}",
                file=sys.stderr,
            )
            skipped += 1
            continue

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        restored += 1

    if not dry_run:
        print(f"[restore] Done. Restored: {restored}, Skipped: {skipped}.")
    else:
        print(
            f"[restore --dry-run] Would restore {len(entries)} file(s). No changes made."
        )
    return 0


# ── purge subcommand ───────────────────────────────────────────────────────────


def cmd_purge(args: argparse.Namespace) -> int:
    """Permanently delete quarantined files."""
    plan_id: str = args.plan_id
    maildir_root: str = os.path.abspath(args.maildir_root)
    confirm: bool = args.confirm
    force: bool = getattr(args, "force", False)
    dry_run: bool = args.dry_run

    if not confirm:
        print("[ERROR] --confirm flag required to purge.", file=sys.stderr)
        return 1

    audit_path = os.path.join(_quarantine_base(maildir_root, plan_id), _AUDIT_FILENAME)
    if not os.path.isfile(audit_path):
        print(f"[ERROR] Audit file not found: {audit_path}", file=sys.stderr)
        return 1

    entries = _read_audit(audit_path)

    # Retention check: first entry's moved_at must be >= 7 days ago
    if entries and not force:
        first_moved_at_str = entries[0].get("moved_at", "")
        try:
            first_moved_at = _parse_iso(first_moved_at_str)
        except ValueError:
            print(
                f"[ERROR] Cannot parse moved_at {first_moved_at_str!r}. Use --force to override.",
                file=sys.stderr,
            )
            return 1

        age = _utcnow() - first_moved_at
        if age < timedelta(days=_PURGE_RETENTION_DAYS):
            days_remaining = _PURGE_RETENTION_DAYS - age.days
            print(
                f"[ERROR] Quarantine is only {age.days} day(s) old "
                f"(minimum {_PURGE_RETENTION_DAYS} days required; {days_remaining} day(s) remaining). "
                "Use --force to override.",
                file=sys.stderr,
            )
            return 1

    deleted = 0
    skipped = 0

    for entry in entries:
        qpath = entry.get("quarantine_path", "")
        if dry_run:
            print(f"[purge --dry-run] Would delete: {qpath!r}")
            continue

        if not os.path.isfile(qpath):
            print(f"[purge] File not found, skipping: {qpath!r}", file=sys.stderr)
            skipped += 1
            continue

        os.remove(qpath)
        deleted += 1

    if not dry_run:
        print(f"[purge] Done. Deleted: {deleted}, Skipped: {skipped}.")
    else:
        print(
            f"[purge --dry-run] Would delete {len(entries)} file(s). No changes made."
        )
    return 0


# ── audit helpers ──────────────────────────────────────────────────────────────


def _read_audit(audit_path: str) -> list[dict[str, Any]]:
    """Read audit.jsonl and return a list of entry dicts."""
    entries: list[dict[str, Any]] = []
    with open(audit_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


# ── argument parser ────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maildir_report.apply_decisions",
        description=(
            "Apply reviewed email decisions to a local Maildir: quarantine, restore, or purge."
        ),
    )

    # Global flags
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would happen but do not move or delete any files.",
    )
    parser.add_argument(
        "--break-glass",
        action="store_true",
        default=False,
        help=f"Lift the {_FILE_CAP:,}-file safety cap.",
    )

    subparsers = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subparsers.required = True

    # Shared parent parser for --dry-run / --break-glass so subcommands also
    # accept them in the post-subcommand position (e.g. plan ... --dry-run).
    _shared = argparse.ArgumentParser(add_help=False)
    _shared.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would happen but do not move or delete any files.",
    )
    _shared.add_argument(
        "--break-glass",
        action="store_true",
        default=False,
        help=f"Lift the {_FILE_CAP:,}-file safety cap.",
    )

    # ── plan ──
    p_plan = subparsers.add_parser(
        "plan",
        parents=[_shared],
        help="Read decisions CSV and write a cleanup_plan.json.",
        description=(
            "Scan decisions CSV for 'delete' rows, resolve file paths under maildir_root, "
            "compute a deterministic candidate_set_hash, and write cleanup_plan.json."
        ),
    )
    p_plan.add_argument(
        "--maildir-root",
        required=True,
        metavar="PATH",
        help="Root of the Maildir to operate on.",
    )
    p_plan.add_argument(
        "--decisions-csv",
        required=True,
        metavar="PATH",
        help="Path to the reviewed decisions CSV.",
    )
    p_plan.add_argument(
        "--mode",
        choices=["quarantine", "delete"],
        default="quarantine",
        help="Cleanup mode (default: quarantine).",
    )
    p_plan.add_argument(
        "--output-plan",
        metavar="PATH",
        default="cleanup_plan.json",
        help="Where to write the plan JSON (default: cleanup_plan.json).",
    )
    p_plan.set_defaults(func=cmd_plan)

    # ── apply ──
    p_apply = subparsers.add_parser(
        "apply",
        parents=[_shared],
        help="Move files listed in cleanup_plan.json to quarantine.",
        description=(
            "Reads cleanup_plan.json, validates the hash prefix, and moves candidate "
            "files to the .quarantine/<plan_id>/ directory."
        ),
    )
    p_apply.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="Path to cleanup_plan.json.",
    )
    p_apply.add_argument(
        "--confirm",
        required=True,
        metavar="HASH_PREFIX",
        help=f"First {_CONFIRM_MIN_LEN}+ chars of candidate_set_hash to confirm intent.",
    )
    p_apply.set_defaults(func=cmd_apply)

    # ── restore ──
    p_restore = subparsers.add_parser(
        "restore",
        parents=[_shared],
        help="Move quarantined files back to their original paths.",
        description=(
            "Reads audit.jsonl from the quarantine directory and moves files back to "
            "their original paths."
        ),
    )
    p_restore.add_argument(
        "--plan-id",
        required=True,
        metavar="PLAN_ID",
        help="UUID of the plan whose quarantined files should be restored.",
    )
    p_restore.add_argument(
        "--maildir-root",
        required=True,
        metavar="PATH",
        help="Root of the Maildir.",
    )
    p_restore.set_defaults(func=cmd_restore)

    # ── purge ──
    p_purge = subparsers.add_parser(
        "purge",
        parents=[_shared],
        help="Permanently delete quarantined files (requires 7-day retention).",
        description=(
            "Deletes all quarantined files for a plan. By default refuses if the "
            "quarantine is younger than 7 days. Use --force to override."
        ),
    )
    p_purge.add_argument(
        "--plan-id",
        required=True,
        metavar="PLAN_ID",
        help="UUID of the plan to purge.",
    )
    p_purge.add_argument(
        "--maildir-root",
        required=True,
        metavar="PATH",
        help="Root of the Maildir.",
    )
    p_purge.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help="Required flag to confirm destructive deletion.",
    )
    p_purge.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Override the 7-day retention check.",
    )
    p_purge.set_defaults(func=cmd_purge)

    return parser


# ── entry point ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate subcommand.

    Returns an exit code (0 = success, non-zero = error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Forward global flags to subcommand functions via the namespace
    # (they are already set on args by the top-level parser)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

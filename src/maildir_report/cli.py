"""
cli.py — Command-line entrypoint for maildir_report.

Usage
-----
    python -m maildir_report <maildir_path> <output_dir> --timestamp <ISO8601>

    # or via the installed script entrypoint (if configured in pyproject.toml):
    maildir-report <maildir_path> <output_dir> --timestamp <ISO8601>

Design rules
------------
- NO datetime.now() calls.  The --timestamp flag is REQUIRED; there is no
  wall-clock fallback.  This enforces byte-for-byte determinism across runs.
- Strict mode is ON by default: any unreadable/unparseable mail file causes
  a non-zero exit with a human-readable error message that includes the path.
- Inventory reconciliation is always run: files on disk must match parsed
  records 1:1 or the run fails with a clear error.
- All three output artifacts are always written in one run:
    <output_dir>/report.pdf          — deterministic German PDF
    <output_dir>/manifest.json       — audited JSON manifest (pdf_sha256 included)
    <output_dir>/decisions.csv       — editable decisions template

Exit codes
----------
0   — success (all three outputs written)
1   — any error (parse failure, inventory mismatch, bad timestamp, I/O error)

Public API
----------
main() -> int
    Parse sys.argv and execute the full pipeline.  Returns exit code (0 or 1).
    Designed to be called from __main__.py or a setuptools entry_point.

build_pipeline(maildir_path, output_dir, timestamp_str) -> None
    Execute the full pipeline without touching sys.argv or sys.exit.
    Raises on any error — useful for programmatic/test invocation.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from maildir_report.dedup import group_emails
from maildir_report.decisions_template import (
    generate_decisions_template,
    serialize_decisions_csv,
)
from maildir_report.inventory import reconcile_inventory
from maildir_report.manifest import build_manifest
from maildir_report.parser import MailParseError, scan_maildir
from maildir_report.pdf import build_report_pdf


# ── output filenames ───────────────────────────────────────────────────────────

PDF_FILENAME = "report.pdf"
MANIFEST_FILENAME = "manifest.json"
DECISIONS_FILENAME = "decisions.csv"


# ── pipeline ──────────────────────────────────────────────────────────────────


def build_pipeline(
    maildir_path: str,
    output_dir: str,
    timestamp_str: str,
) -> None:
    """Execute the full Maildir → PDF + manifest + decisions pipeline.

    Parameters
    ----------
    maildir_path:
        Path to the Maildir root directory.  Must contain ``cur/`` and/or
        ``new/`` sub-directories.
    output_dir:
        Directory where output files are written.  Created if it does not
        exist.  Any pre-existing files with the canonical names are
        overwritten.
    timestamp_str:
        ISO 8601 datetime string for the report generation timestamp.
        Passed verbatim to ``runtime.parse_report_timestamp()``; raises
        ``ValueError`` on bad input.  Date-only strings are rejected.

    Raises
    ------
    MailParseError
        If any mail file cannot be read or parsed (strict mode).
    maildir_report.inventory.InventoryMismatchError
        If the set of files on disk differs from parsed records.
    ValueError
        If ``timestamp_str`` is not a valid ISO 8601 datetime.
    OSError
        If ``maildir_path`` does not exist or ``output_dir`` cannot be
        created/written.
    """
    # ── 1. Scan + parse ───────────────────────────────────────────────────────
    records: list[dict[str, Any]] = scan_maildir(maildir_path)

    # ── 2. Inventory reconciliation ───────────────────────────────────────────
    # Raises InventoryMismatchError if any file is missing or extra.
    reconcile_inventory(maildir_path, records)

    # ── 3. Duplicate grouping ─────────────────────────────────────────────────
    annotated_records, dup_groups = group_emails(records)

    # ── 4. Generate PDF ───────────────────────────────────────────────────────
    pdf_bytes: bytes = build_report_pdf(annotated_records, dup_groups, timestamp_str)

    # ── 5. Build manifest (includes pdf_sha256) ───────────────────────────────
    manifest: dict[str, Any] = build_manifest(
        annotated_records,
        dup_groups,
        timestamp_str,
        pdf_bytes=pdf_bytes,
    )

    # ── 6. Build decisions template ───────────────────────────────────────────
    decisions_rows = generate_decisions_template(annotated_records)
    decisions_csv: str = serialize_decisions_csv(decisions_rows)

    # ── 7. Write outputs ──────────────────────────────────────────────────────
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / PDF_FILENAME).write_bytes(pdf_bytes)
    (out / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / DECISIONS_FILENAME).write_text(decisions_csv, encoding="utf-8")


# ── argument parser ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maildir-report",
        description=(
            "Generate a deterministic German PDF report from a Maildir directory.\n\n"
            "Three output artifacts are always written to OUTPUT_DIR:\n"
            f"  {PDF_FILENAME}       — German PDF with email list and duplicate groups\n"
            f"  {MANIFEST_FILENAME}  — Audited JSON manifest (includes PDF SHA-256)\n"
            f"  {DECISIONS_FILENAME} — Editable decisions template (CSV)\n\n"
            "Strict mode is always on: any unreadable mail file causes a non-zero exit."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "maildir_path",
        metavar="MAILDIR",
        help="Path to the Maildir root directory (must contain cur/ and/or new/).",
    )
    parser.add_argument(
        "output_dir",
        metavar="OUTPUT_DIR",
        help="Directory where report.pdf, manifest.json, and decisions.csv are written.",
    )
    parser.add_argument(
        "--timestamp",
        required=True,
        metavar="ISO8601",
        help=(
            "Report generation timestamp in ISO 8601 format "
            "(e.g. '2024-06-15T10:00:00+00:00').  REQUIRED — no wall-clock fallback."
        ),
    )
    parser.add_argument(
        "--source",
        choices=["rsync", "imap"],
        default="rsync",
        help=(
            "Acquisition source for the Maildir.  'rsync' (default) uses the "
            "MAILDIR positional arg directly.  'imap' fetches from an IMAP server "
            "using env vars IMAP_SERVER/IMAP_USER/IMAP_PASS and materialises a local "
            "Maildir under DATA_DIR/imap/<mailbox>/INBOX/Maildir/ before scanning."
        ),
    )
    parser.add_argument(
        "--imap-mailbox",
        default=None,
        metavar="MAILBOX",
        help="Mailbox name for IMAP ingest (required when --source imap).",
    )
    parser.add_argument(
        "--imap-since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Fetch IMAP messages on or after this date (optional; --source imap only).",
    )
    return parser


# ── entrypoint ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the full pipeline.

    Parameters
    ----------
    argv:
        Argument list.  Defaults to ``sys.argv[1:]`` when ``None``.

    Returns
    -------
    int
        ``0`` on success, ``1`` on any error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve the Maildir path depending on --source
    if args.source == "imap":
        # --source imap: positional arg is data_dir; --imap-mailbox is required.
        if not args.imap_mailbox:
            print(
                "ERROR: --imap-mailbox is required when --source imap",
                file=sys.stderr,
            )
            return 1
        try:
            from maildir_report.imap_ingest import (
                ImapCredentialError,
                ImapIngestConfig,
                run_imap_ingest,
            )
        except ImportError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        try:
            imap_cfg = ImapIngestConfig.from_env(
                mailbox_name=args.imap_mailbox,
                data_dir=args.maildir_path,
                since=args.imap_since,
            )
        except ImapCredentialError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        try:
            maildir_root = run_imap_ingest(config=imap_cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        # Pipeline scans the materialised Maildir root (contains cur/ inside)
        maildir_path = str(maildir_root)
    else:
        maildir_path = args.maildir_path

    try:
        build_pipeline(
            maildir_path=maildir_path,
            output_dir=args.output_dir,
            timestamp_str=args.timestamp,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0

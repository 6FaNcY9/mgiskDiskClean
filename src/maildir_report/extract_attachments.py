"""
extract_attachments.py — Extract MIME attachments from a Maildir to disk.

Purpose
-------
Scan a stored Maildir and write every attachment (and named inline part) as a
file under::

    <output_root>/<sha256>_<size>.<ext>

where ``<ext>`` is derived from the original filename (or ``bin`` when absent).

The naming is collision-safe: two different payloads have different SHA-256
digests, so they always land in different files.  Two payloads with the *same*
bytes (same ``sha256``) are *identical content* — the second write is silently
skipped (idempotence).

Scope
-----
- Writes ONLY to ``<output_root>/`` (the ``attachments/`` sub-directory of the
  mailbox data root).
- Never modifies the Maildir itself.
- Never deletes attachments already on disk.

Path-safety guarantees
----------------------
- Attachment filenames from MIME headers can contain directory separators or
  ``..`` fragments.  This module uses ONLY the ``sha256_<size>.<ext>`` naming
  — the original filename is NEVER used as the on-disk path component.
- ``<ext>`` is derived by taking the suffix of the original filename and
  stripping any path separator characters; falls back to ``"bin"`` when the
  extension is missing or would be empty.

Collision-safe writes
---------------------
When a file already exists at the target path:
- If its content matches (SHA-256 of on-disk bytes == expected SHA-256), the
  write is skipped (idempotent).
- If its content differs (hash collision — probability negligible for SHA-256),
  a ``RuntimeError`` is raised rather than silently overwriting data.

Public API
----------
ExtractResult
    Dataclass returned by ``extract_attachments()``.

extract_attachments(maildir_root, output_root) -> ExtractResult
    Main extraction function.  Called programmatically or from ``main()``.

main(argv=None) -> int
    CLI entrypoint.  ``python -m maildir_report.extract_attachments --help``.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

from maildir_report.hash import sha256_hex
from maildir_report.parser import scan_maildir


# ── result dataclass ──────────────────────────────────────────────────────────


@dataclass
class ExtractResult:
    """Result of an extraction run.

    Attributes
    ----------
    written : list[str]
        Absolute paths of files written during this run (new files only).
    skipped_duplicate : list[str]
        Absolute paths skipped because the same content already existed.
    total_attachments : int
        Total attachment parts encountered (written + skipped combined).
    output_root : str
        Absolute path of the output directory.
    """

    written: list[str] = field(default_factory=list)
    skipped_duplicate: list[str] = field(default_factory=list)
    total_attachments: int = 0
    output_root: str = ""


# ── path helpers ──────────────────────────────────────────────────────────────

# MIME types treated as body text — not extractable as stand-alone attachments.
_BODY_ONLY_MIME = frozenset(
    {
        "text/plain",
        "text/html",
        "multipart/mixed",
        "multipart/alternative",
        "multipart/related",
        "multipart/signed",
        "message/rfc822",
    }
)

# Part category labels that are never written to disk as files.
_SKIP_CATEGORIES = frozenset({"nested_message", "other"})


def _safe_extension(original_filename: str) -> str:
    """Return a path-safe file extension (without the leading dot).

    Derives the extension from *original_filename* suffix.  Any path separator
    character (``/``, ``\\``) in the extension is stripped.  Falls back to
    ``"bin"`` when the result would be empty.

    Parameters
    ----------
    original_filename:
        The original filename from the MIME header (may be a path or empty).

    Returns
    -------
    str
        A safe extension string like ``"pdf"``, ``"jpg"``, or ``"bin"``.
    """
    if not original_filename:
        return "bin"
    # Use pathlib to extract the suffix from the final component only.
    stem_name = pathlib.PurePosixPath(original_filename).name
    # Also guard against Windows-style paths in MIME headers.
    stem_name = stem_name.split("\\")[-1]
    suffix = pathlib.Path(stem_name).suffix  # includes leading dot or ""
    # Strip the leading dot and any remaining path characters.
    ext = suffix.lstrip(".").replace("/", "").replace("\\", "").strip()
    return ext if ext else "bin"


def _stored_filename(sha256: str, size: int, original_filename: str) -> str:
    """Return the on-disk filename for an attachment.

    Format: ``<sha256>_<size>.<ext>``

    Parameters
    ----------
    sha256:
        64-char lowercase hex SHA-256 digest of the payload.
    size:
        Byte-length of the payload.
    original_filename:
        Original MIME filename (used only to derive the extension).

    Returns
    -------
    str
        A filename string safe for all POSIX filesystems.
    """
    ext = _safe_extension(original_filename)
    return f"{sha256}_{size}.{ext}"


# ── extraction logic ──────────────────────────────────────────────────────────


def _is_extractable_part(part: dict[str, Any]) -> bool:
    """Return True if *part* should be written to disk.

    Parts are excluded when:
    - They have an empty payload (``size == 0`` AND ``payload_bytes`` is empty
      or None) — zero-byte files carry no useful content.
    - Their category is in ``_SKIP_CATEGORIES``.
    - Their MIME type is a body-only container type AND they have no real
      filename (e.g. ``text/plain`` body without a name attribute).
    """
    category = part.get("category", "")
    if category in _SKIP_CATEGORIES:
        return False

    mime = part.get("mime", "")
    filename = part.get("filename", "")

    # Skip nameless body parts (structural MIME containers).
    if mime in _BODY_ONLY_MIME and not filename:
        return False

    # Synthetic labels like "[inline plain]" are NOT real filenames.
    if filename.startswith("[inline ") and filename.endswith("]"):
        return False

    # Skip zero-byte parts — nothing to write.
    size = part.get("size", 0)
    if size == 0:
        return False

    return True


def extract_attachments(
    maildir_root: str,
    output_root: str,
) -> ExtractResult:
    """Extract attachments from a Maildir tree to *output_root*.

    Scans *maildir_root* using ``parser.scan_maildir()`` and writes each
    extractable MIME part to::

        <output_root>/<sha256>_<size>.<ext>

    Already-present identical files are silently skipped (idempotent).

    Parameters
    ----------
    maildir_root:
        Path to the Maildir root directory (contains ``cur/``, ``new/``).
    output_root:
        Directory where attachment files will be written.  Created if absent.

    Returns
    -------
    ExtractResult
        Summary of what was written, skipped, and total counts.

    Raises
    ------
    RuntimeError
        If an existing file at the target path has different content than the
        part being written (SHA-256 collision — should never happen in practice
        but is guarded against).
    """
    out_dir = pathlib.Path(output_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = ExtractResult(output_root=str(out_dir.resolve()))

    emails = scan_maildir(maildir_root)

    for email_rec in emails:
        parts: list[dict[str, Any]] = email_rec.get("parts", [])
        for part in parts:
            if not _is_extractable_part(part):
                continue

            result.total_attachments += 1

            sha256 = part.get("content_hash", "")
            size = part.get("size", 0)
            original_filename = part.get("filename", "")
            payload: bytes | bytearray | None = part.get("payload_bytes")
            payload_bytes = payload if isinstance(payload, (bytes, bytearray)) else b""

            if not sha256:
                # Recompute if missing (defensive).
                sha256 = sha256_hex(payload_bytes)

            stored_name = _stored_filename(sha256, size, original_filename)
            dest_path = out_dir / stored_name

            if dest_path.exists():
                # Verify same content — guard against collision.
                on_disk_hash = sha256_hex(dest_path.read_bytes())
                if on_disk_hash == sha256:
                    result.skipped_duplicate.append(str(dest_path))
                    continue
                else:
                    raise RuntimeError(
                        f"SHA-256 collision at {dest_path}: "
                        f"existing={on_disk_hash!r}, expected={sha256!r}"
                    )

            dest_path.write_bytes(payload_bytes)
            result.written.append(str(dest_path))

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for attachment extraction.

    Usage
    -----
    python -m maildir_report.extract_attachments \\
        --maildir-root <path> \\
        --output-root <path>

    Returns
    -------
    int
        Exit code: 0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(
        prog="maildir-extract-attachments",
        description=(
            "Extract MIME attachments from a Maildir to a flat output directory.\n\n"
            "Files are written as <sha256>_<size>.<ext> — collision-safe and idempotent.\n"
            "Running the same extraction twice yields no duplicate files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--maildir-root",
        required=True,
        metavar="PATH",
        help="Path to the Maildir root directory (must contain cur/ or new/).",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        metavar="PATH",
        help="Directory to write extracted attachment files into (created if absent).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress progress output.",
    )

    args = parser.parse_args(argv)

    try:
        result = extract_attachments(
            maildir_root=args.maildir_root,
            output_root=args.output_root,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(
            f"[extract-attachments] written={len(result.written)}"
            f" skipped={len(result.skipped_duplicate)}"
            f" total={result.total_attachments}"
            f" output={result.output_root}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

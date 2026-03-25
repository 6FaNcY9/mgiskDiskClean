"""
manifest.py — Audited JSON manifest generator for maildir_report.

Design rules
------------
- NO datetime.now() calls.  The report timestamp is always supplied by the
  caller as an ISO 8601 string and parsed via runtime.parse_report_timestamp().
- Determinism: all lists are ordered via the canonical ordering functions from
  ordering.py.  The manifest is a plain dict that round-trips through
  json.dumps/json.loads without loss of fidelity.
- Reconciliation invariants (machine-verifiable by auditors):
    email_count == len(email_stable_ids)
    dup_email_count <= email_count
    dup_group_count == len(dup_groups)
    sum(g["member_count"] for g in dup_groups) == dup_email_count
- PDF sha256: included as a hex string so auditors can verify the manifest
  was generated from the same PDF bytes they hold.

Manifest schema
---------------
{
    "schema_version": "1.0",
    "generated_at": "<ISO 8601 UTC>",

    "inventory": {
        "email_count":      <int>,   # total parsed email records
        "dup_email_count":  <int>,   # emails that are in at least one dup group
        "dup_group_count":  <int>,   # number of distinct duplicate groups
        "total_size_bytes": <int>,   # sum of total_size across all records
    },

    "email_stable_ids": [<str>, ...],   # ordered by sort_emails(records) — one per record

    "dup_groups": [                     # ordered by sort_dup_groups(dup_groups)
        {
            "group_id":          <str>,
            "member_count":      <int>,
            "member_email_ids":  [<str>, ...],
            "canonical_email_id": <str>,
            "total_size_bytes":  <int>,
        },
        ...
    ],

    "pdf_sha256": "<hex str | None>",   # None when no PDF was generated
}

Public API
----------
build_manifest(
    records, dup_groups, timestamp_str, pdf_bytes=None
) -> dict
    Build and return the manifest dict.

validate_manifest_invariants(manifest) -> None
    Assert reconciliation invariants.  Raises ManifestInvariantError on failure.
"""

from __future__ import annotations

from typing import Any

from maildir_report.hash import sha256_hex
from maildir_report.ordering import sort_dup_groups, sort_emails
from maildir_report.runtime import format_report_timestamp, parse_report_timestamp

SCHEMA_VERSION = "1.0"


# ── typed exception ────────────────────────────────────────────────────────────


class ManifestInvariantError(Exception):
    """Raised when a manifest fails its internal reconciliation invariants.

    Attributes
    ----------
    violations : list[str]
        Human-readable descriptions of each violated invariant.
    """

    def __init__(self, violations: list[str]) -> None:
        self.violations: list[str] = list(violations)
        summary = "; ".join(violations) if violations else "unknown invariant violation"
        super().__init__(f"Manifest invariant violation — {summary}")


# ── public API ─────────────────────────────────────────────────────────────────


def build_manifest(
    records: list[dict[str, Any]],
    dup_groups: list[dict[str, Any]],
    timestamp_str: str,
    pdf_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Build and return the audited manifest dict.

    Parameters
    ----------
    records:
        List of EmailRecord dicts as produced by ``parser.scan_maildir`` and
        annotated by ``dedup.group_emails``.  Each record must have
        ``"stable_id"``, ``"total_size"``, ``"date"``, and ``"filepath"`` keys.
    dup_groups:
        List of DupGroupRecord dicts as returned by ``dedup.group_emails``.
        May be empty when no duplicates exist.
    timestamp_str:
        ISO 8601 datetime string for the report generation time.  Parsed via
        ``runtime.parse_report_timestamp()``; raises ValueError on bad input.
        Must contain a time component (date-only strings are rejected).
    pdf_bytes:
        Optional raw PDF bytes.  When provided, their SHA-256 hex digest is
        stored in ``"pdf_sha256"``.  When ``None`` the field is stored as
        ``None`` (the field key is always present for schema completeness).

    Returns
    -------
    dict[str, Any]
        Schema-complete manifest dict.  JSON-serialisable (no datetime objects,
        no bytes values).  Passes ``validate_manifest_invariants`` without error.
    """
    # ── 1. Parse and format the report timestamp ──────────────────────────────
    dt = parse_report_timestamp(timestamp_str)
    generated_at = format_report_timestamp(dt)

    # ── 2. Sort records and dup_groups for stable ordering ────────────────────
    sorted_records = sort_emails(records)
    sorted_groups = sort_dup_groups(dup_groups)

    # ── 3. Build inventory counters ───────────────────────────────────────────
    email_count = len(sorted_records)
    total_size_bytes = sum(r.get("total_size", 0) for r in sorted_records)

    # dup_email_count: number of records that are in at least one group
    # (i.e. records with a non-None dup_group_id)
    dup_email_count = sum(
        1 for r in sorted_records if r.get("dup_group_id") is not None
    )
    dup_group_count = len(sorted_groups)

    # ── 4. Build email_stable_ids list (one per record, sorted order) ─────────
    email_stable_ids: list[str] = [
        str(r["stable_id"]) for r in sorted_records if r.get("stable_id")
    ]

    # ── 5. Build dup_groups list ───────────────────────────────────────────────
    manifest_dup_groups: list[dict[str, Any]] = []
    for g in sorted_groups:
        manifest_dup_groups.append(
            {
                "group_id": str(g.get("group_id", "")),
                "member_count": int(g.get("member_count", 0)),
                "member_email_ids": list(g.get("member_email_ids", [])),
                "canonical_email_id": str(g.get("canonical_email_id", "")),
                "total_size_bytes": int(g.get("total_size", 0)),
            }
        )

    # ── 6. Compute PDF sha256 ─────────────────────────────────────────────────
    pdf_sha256: str | None = sha256_hex(pdf_bytes) if pdf_bytes is not None else None

    # ── 7. Assemble manifest ──────────────────────────────────────────────────
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "inventory": {
            "email_count": email_count,
            "dup_email_count": dup_email_count,
            "dup_group_count": dup_group_count,
            "total_size_bytes": total_size_bytes,
        },
        "email_stable_ids": email_stable_ids,
        "dup_groups": manifest_dup_groups,
        "pdf_sha256": pdf_sha256,
    }

    return manifest


def validate_manifest_invariants(manifest: dict[str, Any]) -> None:
    """Assert that the manifest's internal reconciliation invariants hold.

    Invariants checked
    ------------------
    1. ``inventory.email_count == len(email_stable_ids)``
       Every parsed record has exactly one stable ID in the list.
    2. ``inventory.dup_email_count <= inventory.email_count``
       Dup-email count cannot exceed total count.
    3. ``inventory.dup_group_count == len(dup_groups)``
       Group count counter matches the actual group list length.
    4. ``sum(g["member_count"] for g in dup_groups) == inventory.dup_email_count``
       The sum of all group member counts equals the dup-email inventory count.
       (This assumes no email belongs to more than one group — union-find
       guarantees this in the dedup engine.)
    5. All ``email_stable_ids`` are unique (no duplicates).
    6. All ``dup_groups[i].group_id`` values are unique.

    Parameters
    ----------
    manifest:
        A manifest dict as produced by ``build_manifest``.

    Returns
    -------
    None
        Silently returns when all invariants hold.

    Raises
    ------
    ManifestInvariantError
        When one or more invariants are violated, with a list of violation
        descriptions that auditors can act on.
    """
    violations: list[str] = []

    inventory = manifest.get("inventory", {})
    email_count = inventory.get("email_count", -1)
    dup_email_count = inventory.get("dup_email_count", -1)
    dup_group_count = inventory.get("dup_group_count", -1)

    email_stable_ids: list[str] = manifest.get("email_stable_ids", [])
    dup_groups: list[dict[str, Any]] = manifest.get("dup_groups", [])

    # Invariant 1: email_count == len(email_stable_ids)
    if email_count != len(email_stable_ids):
        violations.append(
            f"inventory.email_count={email_count} != len(email_stable_ids)={len(email_stable_ids)}"
        )

    # Invariant 2: dup_email_count <= email_count
    if dup_email_count > email_count:
        violations.append(
            f"inventory.dup_email_count={dup_email_count} > inventory.email_count={email_count}"
        )

    # Invariant 3: dup_group_count == len(dup_groups)
    if dup_group_count != len(dup_groups):
        violations.append(
            f"inventory.dup_group_count={dup_group_count} != len(dup_groups)={len(dup_groups)}"
        )

    # Invariant 4: sum(member_count) == dup_email_count
    summed_members = sum(g.get("member_count", 0) for g in dup_groups)
    if summed_members != dup_email_count:
        violations.append(
            f"sum(g.member_count for g in dup_groups)={summed_members} != "
            f"inventory.dup_email_count={dup_email_count}"
        )

    # Invariant 5: unique email_stable_ids
    if len(email_stable_ids) != len(set(email_stable_ids)):
        violations.append(
            f"email_stable_ids contains duplicates "
            f"({len(email_stable_ids) - len(set(email_stable_ids))} duplicate(s))"
        )

    # Invariant 6: unique group_ids
    group_ids = [g.get("group_id", "") for g in dup_groups]
    if len(group_ids) != len(set(group_ids)):
        violations.append(
            f"dup_groups contains duplicate group_id values "
            f"({len(group_ids) - len(set(group_ids))} duplicate(s))"
        )

    if violations:
        raise ManifestInvariantError(violations)

"""
dedup.py — Duplicate grouping engine for maildir_report.

Design
------
Emails are grouped when they share at least one attachment content hash
(i.e. the same bytes appear in two or more emails' parts).  The grouping
algorithm is **union-find** (path-compressed for correctness; the input
sets are small so performance is irrelevant).

Determinism guarantees
~~~~~~~~~~~~~~~~~~~~~~
- Groups are identified by ``ids.dup_group_stable_id()`` — a SHA-256 of
  the **sorted** member email stable IDs.  The same set of members always
  produces the same group ID regardless of input iteration order.
- The canonical member of each group is the **first element** after the
  group's member list is sorted by ``ordering.sort_emails()`` (i.e. the
  oldest email, with filepath as tiebreaker).  This matches the
  ``dup_rank = 0`` convention from the legacy script but is derived from
  stable sort keys, not from iteration position.
- ``dup_rank`` is the 0-based position of each member within its group
  after ``sort_emails()`` ordering — 0 = oldest / canonical.
- ``is_dup`` on each part is set to ``True`` iff that part's
  ``content_hash`` is a *cross-mail* hash (appears in ≥ 2 emails).
- All list outputs (groups, member_email_ids) are passed through the
  canonical ordering functions before being stored.

Public API
----------
group_emails(records) -> tuple[list[EmailRecord], list[DupGroupRecord]]
    Annotate a list of email records with dedup metadata and return both
    the annotated records and the list of duplicate groups.
"""

from __future__ import annotations

from typing import Any

from maildir_report.ids import dup_group_stable_id
from maildir_report.ordering import sort_emails, sort_dup_groups


# ── union-find helpers ────────────────────────────────────────────────────────


def _make_union_find(n: int) -> list[int]:
    """Return an identity parent array for *n* elements."""
    return list(range(n))


def _find(parent: list[int], x: int) -> int:
    """Path-compressed find."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]  # path halving
        x = parent[x]
    return x


def _union(parent: list[int], a: int, b: int) -> None:
    """Union by index (no rank — input sets are small)."""
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[ra] = rb


# ── main public function ──────────────────────────────────────────────────────


def group_emails(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group emails that share at least one attachment content hash.

    Uses union-find semantics: if email A shares a hash with B, and B shares
    a hash with C, then A, B, and C are all in the same group (transitivity).

    Parameters
    ----------
    records:
        List of EmailRecord dicts as produced by ``parser.parse_email_file``
        or ``parser.scan_maildir``.  Each record must have a ``"stable_id"``
        and a ``"parts"`` list where each part has a ``"content_hash"``.
        The input list is NOT modified; copies of the affected dicts are
        returned.

    Returns
    -------
    tuple[list[EmailRecord], list[DupGroupRecord]]
        ``(annotated_records, dup_groups)``

        *annotated_records* is a new list (same order as *records*) where
        each record has ``dup_group_id``, ``dup_rank``, and ``is_dup``
        fields set.  Records that are not in any duplicate group have
        ``dup_group_id = None`` and ``dup_rank = None``.

        Each part's ``is_dup`` flag is also set: ``True`` iff that part's
        ``content_hash`` is a cross-mail hash.

        *dup_groups* is the list of ``DupGroupRecord`` dicts sorted by
        ``ordering.sort_dup_groups()``.  Empty when no duplicates exist.
    """
    n = len(records)
    if n == 0:
        return [], []

    # ── 1. Build hash → [email indices] map ─────────────────────────────────
    # Count only ONCE per email per hash (avoid counting a hash twice if the
    # same email contains two identical parts).
    hash_to_email_idxs: dict[str, list[int]] = {}
    for idx, record in enumerate(records):
        seen_hashes: set[str] = set()
        for part in record.get("parts", []):
            h = part.get("content_hash", "")
            if h and h not in seen_hashes:
                hash_to_email_idxs.setdefault(h, []).append(idx)
                seen_hashes.add(h)

    # ── 2. Find *cross-mail* hashes (appear in ≥ 2 distinct emails) ─────────
    cross_hashes: dict[str, list[int]] = {
        h: idxs for h, idxs in hash_to_email_idxs.items() if len(idxs) >= 2
    }

    if not cross_hashes:
        # No duplicates — return shallow-copied records with no-dup fields set.
        annotated: list[dict[str, Any]] = []
        for record in records:
            rec = dict(record)
            rec["dup_group_id"] = None
            rec["dup_rank"] = None
            # Ensure all parts have is_dup = False
            new_parts = []
            for part in rec.get("parts", []):
                p = dict(part)
                p["is_dup"] = False
                p["dup_group_id"] = None
                new_parts.append(p)
            rec["parts"] = new_parts
            annotated.append(rec)
        return annotated, []

    # ── 3. Union-Find: merge emails sharing any cross-mail hash ─────────────
    parent = _make_union_find(n)
    for idxs in cross_hashes.values():
        for i in range(1, len(idxs)):
            _union(parent, idxs[0], idxs[i])

    # ── 4. Build root → member indices map (only emails IN any cross group) ──
    # An email is in a group iff it appears in at least one cross-hash entry.
    in_any_cross: set[int] = set()
    for idxs in cross_hashes.values():
        in_any_cross.update(idxs)

    root_to_members: dict[int, list[int]] = {}
    for idx in in_any_cross:
        root = _find(parent, idx)
        root_to_members.setdefault(root, []).append(idx)

    # ── 5. Build DupGroupRecord list and annotate records ───────────────────
    # Make shallow copies so we don't mutate the caller's dicts.
    annotated_records: list[dict[str, Any]] = [dict(r) for r in records]
    # Also shallow-copy parts lists.
    for idx in range(n):
        annotated_records[idx]["parts"] = [
            dict(p) for p in annotated_records[idx].get("parts", [])
        ]

    # Mark is_dup on all parts across all records.
    for idx in range(n):
        for part in annotated_records[idx]["parts"]:
            h = part.get("content_hash", "")
            part["is_dup"] = h in cross_hashes
            # dup_group_id on the part will be set below, only for grouped emails
            if "dup_group_id" not in part:
                part["dup_group_id"] = None

    raw_groups: list[dict[str, Any]] = []

    for root, member_idxs in root_to_members.items():
        # Gather the actual EmailRecord dicts for this group.
        member_records = [annotated_records[i] for i in member_idxs]

        # Sort members by the canonical email ordering (date, filepath).
        sorted_members = sort_emails(member_records)

        # Collect stable IDs from sorted member list.
        member_stable_ids: list[str] = [r["stable_id"] for r in sorted_members]

        # Compute deterministic group ID from sorted member stable IDs.
        gid = dup_group_stable_id(member_stable_ids)

        # Canonical member = first in sort order (oldest / tiebroken by filepath).
        canonical_email_id: str = member_stable_ids[0]

        # Compute total size.
        total_size = sum(r.get("total_size", 0) for r in sorted_members)

        # Assign dup_group_id and dup_rank back onto the annotated record copies.
        for rank, member_rec in enumerate(sorted_members):
            member_rec["dup_group_id"] = gid
            member_rec["dup_rank"] = rank
            # Also propagate group ID to parts that are cross-mail duplicates.
            for part in member_rec.get("parts", []):
                if part.get("is_dup"):
                    part["dup_group_id"] = gid

        raw_groups.append(
            {
                "group_id": gid,
                "member_email_ids": member_stable_ids,
                "member_count": len(member_stable_ids),
                "total_size": total_size,
                "canonical_email_id": canonical_email_id,
            }
        )

    dup_groups = sort_dup_groups(raw_groups)
    return annotated_records, dup_groups

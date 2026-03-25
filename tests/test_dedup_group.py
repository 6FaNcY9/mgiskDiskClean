"""
test_dedup_group.py — Tests for duplicate grouping semantics (Task 6).

Covers:
  - 3 emails sharing one attachment hash => exactly 1 group with 3 members
  - No groups when all content hashes are unique
  - Transitive grouping: A shares with B, B shares with C => one group {A, B, C}
  - Deterministic canonical/rank selection is stable under shuffled input order
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from maildir_report.dedup import group_emails
from maildir_report.ids import email_stable_id, dup_group_stable_id
from maildir_report.ordering import sort_emails


# ── test helpers ──────────────────────────────────────────────────────────────


def _make_record(
    filepath: str,
    message_id: str,
    date: str = "2024-01-01 10:00",
    parts: list[dict[str, Any]] | None = None,
    total_size: int = 1024,
) -> dict[str, Any]:
    """Build a minimal synthetic EmailRecord (as parser.parse_email_file produces)."""
    rec: dict[str, Any] = {
        "filepath": filepath,
        "message_id": message_id,
        "subject": "Test",
        "date": date,
        "date_day": date[:10],
        "sender": "a@example.com",
        "to": "b@example.com",
        "folder": "INBOX",
        "total_size": total_size,
        "parts": parts or [],
        "dup_group_id": None,
        "dup_rank": None,
    }
    rec["stable_id"] = email_stable_id(rec)
    return rec


def _make_part(content_hash: str, filename: str = "file.pdf") -> dict[str, Any]:
    """Build a minimal PartRecord with a given content_hash."""
    return {
        "filename": filename,
        "mime": "application/pdf",
        "size": 128,
        "payload_bytes": None,
        "content_hash": content_hash,
        "category": "pdf",
        "is_dup": False,
        "dup_group_id": None,
        "stable_id": content_hash,  # simplified: hash == stable_id for test purposes
    }


# ── test: 3 emails sharing one attachment hash => exactly 1 group, 3 members ──


class TestThreeMemberSharedHash:
    """3 emails sharing one attachment hash must produce exactly 1 group."""

    def _build_records(self) -> list[dict[str, Any]]:
        shared_hash = "a" * 64  # deterministic fake SHA-256 hex
        return [
            _make_record(
                "/mail/cur/e1.msg",
                "<e1@x.com>",
                date="2024-01-01 09:00",
                parts=[_make_part(shared_hash, "attach.pdf")],
            ),
            _make_record(
                "/mail/cur/e2.msg",
                "<e2@x.com>",
                date="2024-02-01 09:00",
                parts=[_make_part(shared_hash, "attach.pdf")],
            ),
            _make_record(
                "/mail/cur/e3.msg",
                "<e3@x.com>",
                date="2024-03-01 09:00",
                parts=[_make_part(shared_hash, "attach.pdf")],
            ),
        ]

    def test_dedup_group_three_members_produces_one_group(self):
        """3 emails with one shared hash -> exactly 1 DupGroupRecord."""
        records = self._build_records()
        annotated, groups = group_emails(records)
        assert len(groups) == 1, f"Expected 1 group, got {len(groups)}"

    def test_dedup_group_three_members_group_has_three_members(self):
        """The single group contains exactly 3 member email IDs."""
        records = self._build_records()
        _, groups = group_emails(records)
        assert groups[0]["member_count"] == 3
        assert len(groups[0]["member_email_ids"]) == 3

    def test_dedup_group_three_members_annotated_records_have_group_id(self):
        """All 3 annotated records carry the same non-None dup_group_id."""
        records = self._build_records()
        annotated, groups = group_emails(records)
        gid = groups[0]["group_id"]
        assert gid  # not empty/None
        for rec in annotated:
            assert rec["dup_group_id"] == gid, (
                f"Record {rec['filepath']!r} has dup_group_id={rec['dup_group_id']!r}, "
                f"expected {gid!r}"
            )

    def test_dedup_group_three_members_ranks_are_zero_one_two(self):
        """Members get dup_rank 0, 1, 2 (0 = oldest by date, then filepath)."""
        records = self._build_records()
        annotated, groups = group_emails(records)
        ranks = sorted(r["dup_rank"] for r in annotated)
        assert ranks == [0, 1, 2]

    def test_dedup_group_three_members_parts_is_dup_flagged(self):
        """Every part with the shared hash must have is_dup=True."""
        records = self._build_records()
        annotated, _ = group_emails(records)
        for rec in annotated:
            for part in rec["parts"]:
                assert part["is_dup"] is True


# ── test: no groups when all content hashes are unique ──────────────────────


class TestNoDuplicates:
    """When all part content hashes are unique, no groups should be created."""

    def test_dedup_group_unique_hashes_no_groups(self):
        """No shared hashes => empty dup_groups list."""
        records = [
            _make_record(
                "/mail/cur/x1.msg",
                "<x1@x.com>",
                parts=[_make_part("a" * 64, "a.pdf")],
            ),
            _make_record(
                "/mail/cur/x2.msg",
                "<x2@x.com>",
                parts=[_make_part("b" * 64, "b.pdf")],
            ),
            _make_record(
                "/mail/cur/x3.msg",
                "<x3@x.com>",
                parts=[_make_part("c" * 64, "c.pdf")],
            ),
        ]
        annotated, groups = group_emails(records)
        assert groups == [], f"Expected no groups, got {groups!r}"

    def test_dedup_group_unique_hashes_no_group_ids_on_records(self):
        """Records have dup_group_id=None and dup_rank=None when no duplicates."""
        records = [
            _make_record(
                "/mail/cur/u1.msg", "<u1@x.com>", parts=[_make_part("d" * 64)]
            ),
            _make_record(
                "/mail/cur/u2.msg", "<u2@x.com>", parts=[_make_part("e" * 64)]
            ),
        ]
        annotated, _ = group_emails(records)
        for rec in annotated:
            assert rec["dup_group_id"] is None
            assert rec["dup_rank"] is None

    def test_dedup_group_unique_hashes_parts_not_flagged(self):
        """Parts of non-duplicate emails must have is_dup=False."""
        records = [
            _make_record(
                "/mail/cur/v1.msg", "<v1@x.com>", parts=[_make_part("f" * 64)]
            ),
            _make_record(
                "/mail/cur/v2.msg", "<v2@x.com>", parts=[_make_part("0" * 64)]
            ),
        ]
        annotated, _ = group_emails(records)
        for rec in annotated:
            for part in rec["parts"]:
                assert part["is_dup"] is False

    def test_dedup_group_empty_records(self):
        """group_emails([]) returns ([], []) without error."""
        annotated, groups = group_emails([])
        assert annotated == []
        assert groups == []


# ── test: transitive grouping (A↔B, B↔C => {A, B, C}) ──────────────────────


class TestTransitiveGrouping:
    """Union-Find transitivity: if A shares with B and B shares with C,
    all three must be in the same group."""

    def _build_transitive_records(self) -> list[dict[str, Any]]:
        # hash_ab is shared between email A and B
        # hash_bc is shared between email B and C
        hash_ab = "1" * 64
        hash_bc = "2" * 64
        hash_a_only = "3" * 64  # not shared — only in A
        return [
            _make_record(
                "/mail/cur/ta.msg",
                "<ta@x.com>",
                date="2024-01-01 08:00",
                parts=[
                    _make_part(hash_ab, "ab.pdf"),
                    _make_part(hash_a_only, "only_a.pdf"),
                ],
            ),
            _make_record(
                "/mail/cur/tb.msg",
                "<tb@x.com>",
                date="2024-01-02 08:00",
                parts=[_make_part(hash_ab, "ab.pdf"), _make_part(hash_bc, "bc.pdf")],
            ),
            _make_record(
                "/mail/cur/tc.msg",
                "<tc@x.com>",
                date="2024-01-03 08:00",
                parts=[_make_part(hash_bc, "bc.pdf")],
            ),
        ]

    def test_dedup_group_transitive_one_group(self):
        """A↔B and B↔C => exactly 1 group containing all 3 emails."""
        records = self._build_transitive_records()
        _, groups = group_emails(records)
        assert len(groups) == 1, f"Expected 1 transitive group, got {len(groups)}"
        assert groups[0]["member_count"] == 3

    def test_dedup_group_transitive_all_emails_in_group(self):
        """All 3 email stable IDs appear in the single group's member_email_ids."""
        records = self._build_transitive_records()
        annotated, groups = group_emails(records)
        gid = groups[0]["group_id"]
        for rec in annotated:
            assert rec["dup_group_id"] == gid

    def test_dedup_group_transitive_only_shared_parts_flagged(self):
        """Parts with non-shared hashes (hash_a_only) must NOT be is_dup=True."""
        records = self._build_transitive_records()
        annotated, _ = group_emails(records)
        hash_a_only = "3" * 64
        for rec in annotated:
            for part in rec["parts"]:
                if part["content_hash"] == hash_a_only:
                    assert part["is_dup"] is False, (
                        "Non-shared hash must not be flagged as duplicate"
                    )


# ── test: deterministic canonical/rank under shuffled input order ────────────


class TestDeterministicCanonical:
    """Canonical member and dup_rank must be stable regardless of input order."""

    def _build_records_for_stability(self) -> list[dict[str, Any]]:
        shared_hash = "5" * 64
        return [
            _make_record(
                "/mail/cur/sa.msg",
                "<sa@x.com>",
                date="2024-01-01 12:00",
                parts=[_make_part(shared_hash)],
                total_size=100,
            ),
            _make_record(
                "/mail/cur/sb.msg",
                "<sb@x.com>",
                date="2024-01-03 12:00",
                parts=[_make_part(shared_hash)],
                total_size=200,
            ),
            _make_record(
                "/mail/cur/sc.msg",
                "<sc@x.com>",
                date="2024-01-02 12:00",
                parts=[_make_part(shared_hash)],
                total_size=300,
            ),
        ]

    def test_dedup_group_stable_canonical_under_shuffle(self):
        """canonical_email_id must be the same regardless of input list order."""
        records = self._build_records_for_stability()
        _, groups_original = group_emails(records)
        canonical_original = groups_original[0]["canonical_email_id"]

        for seed in (42, 99, 7, 0):
            shuffled = records[:]
            random.seed(seed)
            random.shuffle(shuffled)
            _, groups_shuffled = group_emails(shuffled)
            assert len(groups_shuffled) == 1
            assert groups_shuffled[0]["canonical_email_id"] == canonical_original, (
                f"Seed {seed}: canonical_email_id changed after shuffle"
            )

    def test_dedup_group_stable_ranks_under_shuffle(self):
        """Each email's dup_rank is determined by sort_emails order, not input order."""
        records = self._build_records_for_stability()

        # Determine expected canonical ordering via sort_emails.
        sorted_records = sort_emails(records)
        expected_rank_by_stable_id = {
            r["stable_id"]: rank for rank, r in enumerate(sorted_records)
        }

        for seed in (1, 2, 3):
            shuffled = records[:]
            random.seed(seed)
            random.shuffle(shuffled)
            annotated, _ = group_emails(shuffled)
            for rec in annotated:
                sid = rec["stable_id"]
                assert rec["dup_rank"] == expected_rank_by_stable_id[sid], (
                    f"Seed {seed}: {sid[:8]}... has rank {rec['dup_rank']}, "
                    f"expected {expected_rank_by_stable_id[sid]}"
                )

    def test_dedup_group_stable_group_id_under_shuffle(self):
        """Group ID must be identical regardless of input order."""
        records = self._build_records_for_stability()
        _, groups_original = group_emails(records)
        gid_original = groups_original[0]["group_id"]

        for seed in (10, 20, 30):
            shuffled = records[:]
            random.seed(seed)
            random.shuffle(shuffled)
            _, groups_shuffled = group_emails(shuffled)
            assert groups_shuffled[0]["group_id"] == gid_original, (
                f"Seed {seed}: group_id changed after shuffle"
            )

    def test_dedup_group_stable_canonical_is_oldest_by_sort_order(self):
        """canonical_email_id corresponds to the oldest email (lowest sort key)."""
        records = self._build_records_for_stability()
        # sa.msg has earliest date "2024-01-01" => should be canonical (rank 0)
        annotated, groups = group_emails(records)
        canonical_id = groups[0]["canonical_email_id"]

        # Find the record with rank=0.
        rank_zero = next(r for r in annotated if r["dup_rank"] == 0)
        assert rank_zero["stable_id"] == canonical_id, (
            f"canonical_email_id {canonical_id[:8]!r}... is not the rank-0 member "
            f"{rank_zero['stable_id'][:8]!r}..."
        )
        # And it should be the one with the earliest date.
        assert rank_zero["filepath"] == "/mail/cur/sa.msg"

    def test_dedup_group_stable_member_ids_are_sorted(self):
        """member_email_ids in each group must be deterministically ordered (sort_emails)."""
        records = self._build_records_for_stability()
        _, groups = group_emails(records)
        ids = groups[0]["member_email_ids"]
        # member_email_ids must match sorted order (we verify stable_id ordering by date,filepath)
        sorted_records = sort_emails(records)
        expected_ids = [r["stable_id"] for r in sorted_records]
        assert ids == expected_ids


# ── test: group_id is a valid SHA-256 hex string ─────────────────────────────


def test_dedup_group_id_is_valid_sha256_hex():
    """DupGroupRecord group_id must be a 64-char lowercase hex string."""
    shared_hash = "7" * 64
    records = [
        _make_record("/mail/cur/g1.msg", "<g1@x.com>", parts=[_make_part(shared_hash)]),
        _make_record("/mail/cur/g2.msg", "<g2@x.com>", parts=[_make_part(shared_hash)]),
    ]
    _, groups = group_emails(records)
    assert len(groups) == 1
    gid = groups[0]["group_id"]
    assert isinstance(gid, str)
    assert len(gid) == 64
    assert gid == gid.lower()
    assert all(c in "0123456789abcdef" for c in gid)


# ── test: total_size is summed correctly ─────────────────────────────────────


def test_dedup_group_total_size_is_sum_of_member_sizes():
    """DupGroupRecord total_size must equal sum of member email total_sizes."""
    shared_hash = "9" * 64
    records = [
        _make_record(
            "/mail/cur/s1.msg",
            "<s1@x.com>",
            total_size=100,
            parts=[_make_part(shared_hash)],
        ),
        _make_record(
            "/mail/cur/s2.msg",
            "<s2@x.com>",
            total_size=250,
            parts=[_make_part(shared_hash)],
        ),
    ]
    _, groups = group_emails(records)
    assert groups[0]["total_size"] == 350


# ── test: emails with no parts are not grouped ───────────────────────────────


def test_dedup_group_emails_with_no_parts_not_grouped():
    """Emails that have no parts (or empty parts) cannot share hashes => no group."""
    records = [
        _make_record("/mail/cur/np1.msg", "<np1@x.com>", parts=[]),
        _make_record("/mail/cur/np2.msg", "<np2@x.com>", parts=[]),
    ]
    annotated, groups = group_emails(records)
    assert groups == []
    for rec in annotated:
        assert rec["dup_group_id"] is None
        assert rec["dup_rank"] is None

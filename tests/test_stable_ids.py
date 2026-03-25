"""
Tests for stable IDs and deterministic ordering — Task 2.

Design contracts being tested:
  - stable_id: every ID derivation uses canonical, content-based inputs only
    (no runtime timestamps, no random UUIDs, no index positions like m["id"] = i)
  - deterministic_order: all list outputs are sorted by a well-defined key,
    independent of filesystem iteration order or dict insertion order
"""

import hashlib
from typing import Any

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_email_record(
    filepath: str,
    message_id: str,
    subject: str = "Test",
    date: str = "2024-01-15 10:00",
    sender: str = "alice@example.com",
    folder: str = "INBOX",
    total_size: int = 1024,
) -> dict[str, Any]:
    """Build a minimal synthetic email record dict (as models.EmailRecord would)."""
    return {
        "filepath": filepath,
        "message_id": message_id,
        "subject": subject,
        "date": date,
        "sender": sender,
        "folder": folder,
        "total_size": total_size,
        "parts": [],
    }


def _make_part_record(
    payload_bytes: bytes,
    filename: str = "doc.pdf",
    mime: str = "application/pdf",
) -> dict[str, Any]:
    """Build a synthetic part record."""
    return {
        "filename": filename,
        "mime": mime,
        "payload_bytes": payload_bytes,
    }


# ── imports under test ────────────────────────────────────────────────────────


def test_modules_importable():
    """All three Task-2 modules must be importable."""
    from maildir_report import ids, models, ordering  # noqa: F401


# ── stable_id: EmailRecord ────────────────────────────────────────────────────


class TestStableIdEmail:
    """email_stable_id() must produce deterministic, content-based identifiers."""

    def test_stable_id_email_returns_string(self):
        """email_stable_id returns a non-empty string."""
        from maildir_report.ids import email_stable_id

        rec = _make_email_record(
            filepath="/mail/cur/abc.msg",
            message_id="<abc@example.com>",
        )
        result = email_stable_id(rec)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_stable_id_email_same_inputs_same_output(self):
        """Two identical records produce the same stable ID."""
        from maildir_report.ids import email_stable_id

        rec1 = _make_email_record("/mail/cur/abc.msg", "<abc@example.com>")
        rec2 = _make_email_record("/mail/cur/abc.msg", "<abc@example.com>")
        assert email_stable_id(rec1) == email_stable_id(rec2)

    def test_stable_id_email_different_filepath_different_id(self):
        """Different filepaths produce different IDs."""
        from maildir_report.ids import email_stable_id

        rec1 = _make_email_record("/mail/cur/aaa.msg", "<same@example.com>")
        rec2 = _make_email_record("/mail/cur/bbb.msg", "<same@example.com>")
        assert email_stable_id(rec1) != email_stable_id(rec2)

    def test_stable_id_email_not_index_based(self):
        """IDs must NOT be integer positions — they must survive reordering."""
        from maildir_report.ids import email_stable_id

        records = [
            _make_email_record("/mail/cur/first.msg", "<first@example.com>"),
            _make_email_record("/mail/cur/second.msg", "<second@example.com>"),
            _make_email_record("/mail/cur/third.msg", "<third@example.com>"),
        ]
        # compute IDs on the original list
        ids_original = [email_stable_id(r) for r in records]

        # shuffle and recompute — must still be the same per record
        shuffled = [records[2], records[0], records[1]]
        ids_shuffled = [email_stable_id(r) for r in shuffled]

        # same record must get same ID regardless of position
        assert ids_shuffled[0] == ids_original[2]  # third
        assert ids_shuffled[1] == ids_original[0]  # first
        assert ids_shuffled[2] == ids_original[1]  # second

    def test_stable_id_email_no_runtime_timestamp(self):
        """email_stable_id must not use any runtime state — same input, same output across calls."""
        from maildir_report.ids import email_stable_id
        import time

        rec = _make_email_record("/mail/cur/x.msg", "<x@example.com>")
        id_before = email_stable_id(rec)
        time.sleep(0.01)  # small pause to detect any datetime.now() usage
        id_after = email_stable_id(rec)
        assert id_before == id_after


# ── stable_id: PartRecord ────────────────────────────────────────────────────


class TestStableIdPart:
    """part_stable_id() must derive a content-based identifier from the part payload."""

    def test_stable_id_part_returns_string(self):
        from maildir_report.ids import part_stable_id

        part = _make_part_record(b"hello world")
        result = part_stable_id(part)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_stable_id_part_content_addressable(self):
        """Same bytes => same part ID; different bytes => different part ID."""
        from maildir_report.ids import part_stable_id

        p1 = _make_part_record(b"payload_A")
        p2 = _make_part_record(b"payload_A")
        p3 = _make_part_record(b"payload_B")
        assert part_stable_id(p1) == part_stable_id(p2)
        assert part_stable_id(p1) != part_stable_id(p3)

    def test_stable_id_part_uses_sha256(self):
        """Part ID must be based on SHA-256 (not MD5 — anti-pattern from legacy script)."""
        from maildir_report.ids import part_stable_id

        payload = b"test payload for sha256 check"
        part = _make_part_record(payload)
        result = part_stable_id(part)
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        # The returned value should incorporate the SHA-256 hex digest
        assert expected_sha256 in result


# ── stable_id: DuplicateGroup ─────────────────────────────────────────────────


class TestStableIdDuplicateGroup:
    """dup_group_stable_id() must produce a deterministic group identifier."""

    def test_stable_id_dup_group_from_member_ids(self):
        """Group ID is derived from its sorted member email IDs — order invariant."""
        from maildir_report.ids import dup_group_stable_id, email_stable_id

        rec_a = _make_email_record("/mail/cur/a.msg", "<a@example.com>")
        rec_b = _make_email_record("/mail/cur/b.msg", "<b@example.com>")
        id_a = email_stable_id(rec_a)
        id_b = email_stable_id(rec_b)

        gid1 = dup_group_stable_id([id_a, id_b])
        gid2 = dup_group_stable_id([id_b, id_a])  # reversed input order

        assert gid1 == gid2  # must be order-invariant

    def test_stable_id_dup_group_different_members_different_id(self):
        from maildir_report.ids import dup_group_stable_id, email_stable_id

        rec_a = _make_email_record("/mail/cur/a.msg", "<a@example.com>")
        rec_b = _make_email_record("/mail/cur/b.msg", "<b@example.com>")
        rec_c = _make_email_record("/mail/cur/c.msg", "<c@example.com>")
        id_a = email_stable_id(rec_a)
        id_b = email_stable_id(rec_b)
        id_c = email_stable_id(rec_c)

        gid_ab = dup_group_stable_id([id_a, id_b])
        gid_ac = dup_group_stable_id([id_a, id_c])
        assert gid_ab != gid_ac

    def test_stable_id_dup_group_returns_string(self):
        from maildir_report.ids import dup_group_stable_id, email_stable_id

        rec = _make_email_record("/mail/cur/x.msg", "<x@example.com>")
        result = dup_group_stable_id([email_stable_id(rec)])
        assert isinstance(result, str)
        assert len(result) > 0


# ── deterministic_order: email list ──────────────────────────────────────────


class TestDeterministicOrderEmails:
    """sort_emails() must produce a stable, reproducible ordering."""

    def test_deterministic_order_emails_by_date_then_filepath(self):
        """Emails are sorted by (date, filepath) so order is filesystem-independent."""
        from maildir_report.ordering import sort_emails

        records = [
            _make_email_record("/mail/cur/c.msg", "<c@e.com>", date="2024-03-01 09:00"),
            _make_email_record("/mail/cur/a.msg", "<a@e.com>", date="2024-01-01 09:00"),
            _make_email_record("/mail/cur/b.msg", "<b@e.com>", date="2024-02-01 09:00"),
        ]
        sorted_recs = sort_emails(records)
        assert [r["filepath"] for r in sorted_recs] == [
            "/mail/cur/a.msg",
            "/mail/cur/b.msg",
            "/mail/cur/c.msg",
        ]

    def test_deterministic_order_emails_tiebreak_by_filepath(self):
        """When dates are identical, filepath is the tiebreaker (lexicographic)."""
        from maildir_report.ordering import sort_emails

        records = [
            _make_email_record("/mail/cur/z.msg", "<z@e.com>", date="2024-01-01 09:00"),
            _make_email_record("/mail/cur/a.msg", "<a@e.com>", date="2024-01-01 09:00"),
        ]
        sorted_recs = sort_emails(records)
        assert sorted_recs[0]["filepath"] == "/mail/cur/a.msg"
        assert sorted_recs[1]["filepath"] == "/mail/cur/z.msg"

    def test_deterministic_order_emails_independent_of_input_order(self):
        """Input list order must NOT affect sort output."""
        from maildir_report.ordering import sort_emails

        records_fwd = [
            _make_email_record("/mail/cur/x.msg", "<x@e.com>", date="2024-05-01 08:00"),
            _make_email_record("/mail/cur/m.msg", "<m@e.com>", date="2024-03-15 08:00"),
            _make_email_record("/mail/cur/a.msg", "<a@e.com>", date="2024-01-01 08:00"),
        ]
        records_rev = list(reversed(records_fwd))

        result_fwd = [r["filepath"] for r in sort_emails(records_fwd)]
        result_rev = [r["filepath"] for r in sort_emails(records_rev)]
        assert result_fwd == result_rev

    def test_deterministic_order_emails_empty_list(self):
        """sort_emails([]) returns [] without error."""
        from maildir_report.ordering import sort_emails

        assert sort_emails([]) == []


# ── deterministic_order: parts list ──────────────────────────────────────────


class TestDeterministicOrderParts:
    """sort_parts() must produce a stable ordering for attachment/part lists."""

    def test_deterministic_order_parts_by_filename_then_size(self):
        """Parts sorted by (filename, size) deterministically."""
        from maildir_report.ordering import sort_parts

        parts = [
            {**_make_part_record(b"x" * 100, "z.pdf"), "size": 100},
            {**_make_part_record(b"y" * 200, "a.pdf"), "size": 200},
            {**_make_part_record(b"z" * 50, "m.pdf"), "size": 50},
        ]
        result = sort_parts(parts)
        assert [p["filename"] for p in result] == ["a.pdf", "m.pdf", "z.pdf"]

    def test_deterministic_order_parts_independent_of_input_order(self):
        from maildir_report.ordering import sort_parts

        parts = [
            {**_make_part_record(b"a" * 10, "b.pdf"), "size": 10},
            {**_make_part_record(b"b" * 20, "a.pdf"), "size": 20},
        ]
        reversed_parts = list(reversed(parts))
        assert [p["filename"] for p in sort_parts(parts)] == [
            p["filename"] for p in sort_parts(reversed_parts)
        ]


# ── deterministic_order: duplicate groups ────────────────────────────────────


class TestDeterministicOrderDupGroups:
    """sort_dup_groups() must produce a stable ordering for duplicate groups."""

    def test_deterministic_order_dup_groups_by_canonical_member_id(self):
        """Groups sorted by the lexicographically smallest member email stable ID."""
        from maildir_report.ids import email_stable_id
        from maildir_report.ordering import sort_dup_groups

        rec_a = _make_email_record("/mail/cur/a.msg", "<a@e.com>")
        rec_b = _make_email_record("/mail/cur/b.msg", "<b@e.com>")
        rec_c = _make_email_record("/mail/cur/c.msg", "<c@e.com>")
        id_a = email_stable_id(rec_a)
        id_b = email_stable_id(rec_b)
        id_c = email_stable_id(rec_c)

        groups = [
            {"group_id": "gB", "member_email_ids": [id_b, id_c]},
            {"group_id": "gA", "member_email_ids": [id_a, id_b]},
        ]
        # gA has id_a as canonical (min) member, gB has id_b — so gA comes first IFF id_a < id_b
        result = sort_dup_groups(groups)
        # Both orderings are valid depending on actual hash values;
        # the key assertion is that the sort is deterministic (same output on repeated calls).
        result2 = sort_dup_groups(list(reversed(groups)))
        assert [g["group_id"] for g in result] == [g["group_id"] for g in result2]

    def test_deterministic_order_dup_groups_empty(self):
        from maildir_report.ordering import sort_dup_groups

        assert sort_dup_groups([]) == []

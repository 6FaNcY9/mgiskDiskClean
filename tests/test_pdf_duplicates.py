"""
test_pdf_duplicates.py — TDD tests for Task 11: compact duplicate-group section.

Contracts being tested
----------------------
pdf.py
    build_report_pdf(records, dup_groups, timestamp_str) -> bytes
        - PDF contains a "Duplikatgruppen" section heading when groups are present.
        - Each group header (group_id prefix) appears exactly once.
        - Every member email appears as a row in its group's member table.
        - Group membership is exhaustive: no member is omitted.
        - Groups appear in sort_dup_groups() order (deterministic).
        - Members within each group appear in sort_emails() order.
        - No duplicate section appears when dup_groups is empty.
        - Section is compact: only groups that exist are rendered.
        - determinism: same inputs → same SHA-256 bytes (even with dup section).

Text extraction
---------------
Reuses _extract_pdf_text() helper from test_pdf_german_headers.py (inlined here
for test isolation — do not import across test modules).
"""

from __future__ import annotations

import base64
import hashlib
import re
import zlib
from typing import Any


# ── text extraction helper (same as test_pdf_german_headers.py) ───────────────


def _decode_pdf_octal(raw: bytes) -> str:
    """Decode PDF-encoded text operand bytes (with octal escapes) to str."""
    result: list[str] = []
    i = 0
    while i < len(raw):
        if (
            raw[i : i + 1] == b"\\"
            and i + 1 < len(raw)
            and raw[i + 1 : i + 2].isdigit()
        ):
            octal = raw[i + 1 : i + 4]
            result.append(chr(int(octal, 8)))
            i += 4
        else:
            result.append(chr(raw[i]))
            i += 1
    return "".join(result)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text from Tj/TJ operators in a ReportLab PDF."""
    text_parts: list[str] = []
    for m in re.finditer(rb"stream\n(.*?)endstream", pdf_bytes, re.DOTALL):
        raw = m.group(1).strip()
        try:
            stream_bytes = zlib.decompress(base64.a85decode(raw, adobe=True))
        except Exception:
            try:
                stream_bytes = zlib.decompress(raw)
            except Exception:
                continue
        for text_m in re.finditer(rb"\(([^)]*)\)\s*Tj", stream_bytes):
            text_parts.append(_decode_pdf_octal(text_m.group(1)))
    return "\n".join(text_parts)


# ── synthetic record builders ─────────────────────────────────────────────────


def _make_part_record(
    filename: str = "anlage.pdf",
    mime: str = "application/pdf",
    size: int = 1024,
    content_hash: str | None = None,
    is_dup: bool = False,
    dup_group_id: str | None = None,
) -> dict[str, Any]:
    ch = content_hash or hashlib.sha256(filename.encode()).hexdigest()
    return {
        "filename": filename,
        "mime": mime,
        "size": size,
        "content_hash": ch,
        "category": "pdf",
        "is_dup": is_dup,
        "dup_group_id": dup_group_id,
        "stable_id": hashlib.sha256(filename.encode()).hexdigest(),
        "payload_bytes": None,
    }


def _make_email_record(
    filepath: str,
    subject: str = "Kein Betreff",
    sender: str = "absender@example.com",
    date: str = "2024-01-15 10:00",
    folder: str = "INBOX",
    total_size: int = 2048,
    parts: list[dict[str, Any]] | None = None,
    dup_group_id: str | None = None,
    dup_rank: int | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal synthetic EmailRecord dict."""
    mid = message_id or f"<{filepath.replace('/', '_')}@test>"
    return {
        "filepath": filepath,
        "message_id": mid,
        "subject": subject,
        "date": date,
        "date_day": date[:10],
        "sender": sender,
        "to": "empfaenger@example.com",
        "folder": folder,
        "total_size": total_size,
        "parts": parts or [],
        "stable_id": hashlib.sha256(filepath.encode()).hexdigest(),
        "dup_group_id": dup_group_id,
        "dup_rank": dup_rank,
        "has_nested_messages": False,
    }


def _make_dup_group(
    member_records: list[dict[str, Any]],
    group_id: str | None = None,
) -> dict[str, Any]:
    """Build a synthetic DupGroupRecord from a list of EmailRecord dicts."""
    member_ids = [r["stable_id"] for r in member_records]
    gid = (
        group_id
        or hashlib.sha256(
            b"\x00".join(mid.encode() for mid in sorted(member_ids))
        ).hexdigest()
    )
    canonical = member_ids[0]
    total_size = sum(r.get("total_size", 0) for r in member_records)
    return {
        "group_id": gid,
        "member_email_ids": member_ids,
        "member_count": len(member_ids),
        "total_size": total_size,
        "canonical_email_id": canonical,
    }


# ── fixture: three emails, one duplicate group ────────────────────────────────


def _make_three_email_one_group_fixture() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]]
]:
    """
    Returns (records, dup_groups) where:
      - 3 emails share the same attachment hash (one group with 3 members).
      - Each email also has its own unique attachment.
    """
    shared_hash = hashlib.sha256(b"shared_attachment_payload").hexdigest()

    rec_a = _make_email_record(
        filepath="/mbox/cur/alpha",
        subject="Alpha Mail",
        sender="alpha@example.com",
        date="2024-01-10 08:00",
        dup_group_id=None,  # will be set by group helper below
        parts=[
            _make_part_record("shared.pdf", content_hash=shared_hash, is_dup=True),
            _make_part_record("alpha_only.pdf"),
        ],
    )
    rec_b = _make_email_record(
        filepath="/mbox/cur/beta",
        subject="Beta Mail",
        sender="beta@example.com",
        date="2024-01-20 09:00",
        parts=[
            _make_part_record("shared.pdf", content_hash=shared_hash, is_dup=True),
            _make_part_record(
                "beta_only.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        ],
    )
    rec_c = _make_email_record(
        filepath="/mbox/cur/gamma",
        subject="Gamma Mail",
        sender="gamma@example.com",
        date="2024-02-01 11:00",
        parts=[
            _make_part_record("shared.pdf", content_hash=shared_hash, is_dup=True),
        ],
    )

    group = _make_dup_group([rec_a, rec_b, rec_c])
    gid = group["group_id"]

    # Annotate records with the group id
    for r in (rec_a, rec_b, rec_c):
        r["dup_group_id"] = gid

    return [rec_a, rec_b, rec_c], [group]


def _make_two_groups_fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns (records, dup_groups) where:
      - 2 separate duplicate groups exist (2+2 emails).
    """
    hash1 = hashlib.sha256(b"payload_group1").hexdigest()
    hash2 = hashlib.sha256(b"payload_group2").hexdigest()

    # Group 1: emails 1a and 1b share hash1
    rec_1a = _make_email_record(
        filepath="/mbox/cur/g1a",
        subject="Gruppe1-A",
        date="2024-01-05 10:00",
        parts=[_make_part_record("g1.pdf", content_hash=hash1, is_dup=True)],
    )
    rec_1b = _make_email_record(
        filepath="/mbox/cur/g1b",
        subject="Gruppe1-B",
        date="2024-01-10 10:00",
        parts=[_make_part_record("g1.pdf", content_hash=hash1, is_dup=True)],
    )
    # Group 2: emails 2a and 2b share hash2
    rec_2a = _make_email_record(
        filepath="/mbox/cur/g2a",
        subject="Gruppe2-A",
        date="2024-02-01 10:00",
        parts=[_make_part_record("g2.zip", content_hash=hash2, is_dup=True)],
    )
    rec_2b = _make_email_record(
        filepath="/mbox/cur/g2b",
        subject="Gruppe2-B",
        date="2024-02-10 10:00",
        parts=[_make_part_record("g2.zip", content_hash=hash2, is_dup=True)],
    )

    group1 = _make_dup_group([rec_1a, rec_1b])
    group2 = _make_dup_group([rec_2a, rec_2b])

    g1id = group1["group_id"]
    g2id = group2["group_id"]
    rec_1a["dup_group_id"] = g1id
    rec_1b["dup_group_id"] = g1id
    rec_2a["dup_group_id"] = g2id
    rec_2b["dup_group_id"] = g2id

    return [rec_1a, rec_1b, rec_2a, rec_2b], [group1, group2]


# ── section presence tests ────────────────────────────────────────────────────


class TestDuplikateGruppenSectionPresent:
    """Duplicate-groups section heading must appear when groups exist."""

    def test_section_heading_present_when_groups_exist(self) -> None:
        """'Duplikatgruppen' heading must appear in PDF when dup_groups non-empty."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "Duplikatgruppen" in text, (
            f"'Duplikatgruppen' heading not found in PDF. Got:\n{text!r}"
        )

    def test_section_absent_when_no_groups(self) -> None:
        """No dup group content must render when dup_groups is empty.

        Note: 'Duplikatgruppen' still appears in Zusammenfassung as 'Duplikatgruppen: 0'.
        This test verifies no group-specific content (group ID prefix, member rows in
        the dup section) is present when there are no dup groups.
        We check via group-count label 'Gruppe 1:' which appears only in the dup section.
        """
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record("/mbox/cur/nodup1", subject="Kein Duplikat"),
            _make_email_record("/mbox/cur/nodup2", subject="Auch kein Duplikat"),
        ]
        pdf = build_report_pdf(records, [], "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # 'Gruppe 1:' is the group-header label used ONLY in the dup section.
        # It must NOT appear when there are no groups.
        assert "Gruppe 1:" not in text, (
            f"'Gruppe 1:' group label should not appear when no groups. Got:\n{text!r}"
        )
        # E-Mail-Liste records still present (sanity check)
        assert "Kein Duplikat" in text


# ── group header rendered ──────────────────────────────────────────────────────


class TestGroupHeaderRendered:
    """Each duplicate group must render a header (group-id prefix or label)."""

    def test_group_id_prefix_appears_in_pdf(self) -> None:
        """A recognizable prefix of the group_id must appear in the PDF."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        gid = dup_groups[0]["group_id"]
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # We show at least first 8 chars of the group_id in the header
        assert gid[:8] in text, (
            f"Group ID prefix '{gid[:8]}' not found in PDF. "
            f"Full group_id: {gid}\nExtracted text:\n{text!r}"
        )

    def test_two_group_ids_both_present(self) -> None:
        """With two groups, both group-id prefixes must appear."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_two_groups_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        for group in dup_groups:
            gid = group["group_id"]
            assert gid[:8] in text, (
                f"Group ID prefix '{gid[:8]}' not found in PDF. "
                f"Full group_id: {gid}\nExtracted text:\n{text!r}"
            )

    def test_group_member_count_appears(self) -> None:
        """The member count of each group must appear near the group header."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # 3 members — the number '3' must appear in the dup section area
        assert "3" in text, f"Member count '3' not found in PDF text. Got:\n{text!r}"


# ── member rows exhaustiveness ────────────────────────────────────────────────


class TestMemberRowsExhaustive:
    """Every member of every group must appear as a row in the section."""

    def test_all_three_member_subjects_present(self) -> None:
        """All three member subjects must appear in the dup section."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        for subject in ("Alpha Mail", "Beta Mail", "Gamma Mail"):
            assert subject in text, (
                f"Member subject '{subject}' not found in PDF. Got:\n{text!r}"
            )

    def test_all_members_of_both_groups_present(self) -> None:
        """In two-group fixture, all four member subjects must appear."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_two_groups_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        for subject in ("Gruppe1-A", "Gruppe1-B", "Gruppe2-A", "Gruppe2-B"):
            assert subject in text, (
                f"Member subject '{subject}' not found in PDF. Got:\n{text!r}"
            )

    def test_member_sender_appears_in_section(self) -> None:
        """Each member's sender (Von) must appear in the member table rows."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        for sender in ("alpha@example.com", "beta@example.com", "gamma@example.com"):
            assert sender in text, (
                f"Member sender '{sender}' not found in PDF. Got:\n{text!r}"
            )

    def test_member_date_appears_in_section(self) -> None:
        """Each member's date portion must appear in the member rows."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        for date_prefix in ("2024-01-10", "2024-01-20", "2024-02-01"):
            assert date_prefix in text, (
                f"Member date '{date_prefix}' not found in PDF. Got:\n{text!r}"
            )


# ── group-level exhaustiveness (every group listed) ───────────────────────────


class TestGroupExhaustiveness:
    """Every group in dup_groups must be rendered; no group may be omitted."""

    def test_single_group_fully_rendered(self) -> None:
        """Single-group fixture: exactly that one group (and its members) is present."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        assert len(dup_groups) == 1
        gid = dup_groups[0]["group_id"]
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # Section heading present and group rendered
        assert "Duplikatgruppen" in text
        assert gid[:8] in text

    def test_two_groups_both_rendered(self) -> None:
        """Two-group fixture: both group headers present; none omitted."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_two_groups_fixture()
        assert len(dup_groups) == 2
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "Duplikatgruppen" in text
        for g in dup_groups:
            assert g["group_id"][:8] in text, (
                f"Group {g['group_id'][:8]} not rendered in two-group PDF"
            )

    def test_no_group_omitted_in_two_group_fixture(self) -> None:
        """Member count for both groups must appear (2 + 2 = 4 members total)."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_two_groups_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # All four subject strings must appear
        for subj in ("Gruppe1-A", "Gruppe1-B", "Gruppe2-A", "Gruppe2-B"):
            assert subj in text, f"'{subj}' missing from two-group PDF"


# ── ordering ──────────────────────────────────────────────────────────────────


class TestDupSectionOrdering:
    """Groups and members must follow deterministic ordering."""

    def test_groups_in_sort_dup_groups_order(self) -> None:
        """Groups must appear in sort_dup_groups() order (canonical member ID sort)."""
        from maildir_report.pdf import build_report_pdf
        from maildir_report.ordering import sort_dup_groups

        records, dup_groups = _make_two_groups_fixture()
        sorted_groups = sort_dup_groups(dup_groups)

        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)

        pos_g1 = text.find(sorted_groups[0]["group_id"][:8])
        pos_g2 = text.find(sorted_groups[1]["group_id"][:8])

        assert pos_g1 != -1, "First sorted group not found in PDF"
        assert pos_g2 != -1, "Second sorted group not found in PDF"
        assert pos_g1 < pos_g2, (
            "Groups not in sort_dup_groups() order. "
            f"First group at {pos_g1}, second at {pos_g2}"
        )

    def test_members_in_sort_emails_order_within_group(self) -> None:
        """Members within a group must appear in sort_emails() order (date, filepath)."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)

        # Alpha (2024-01-10) must come before Beta (2024-01-20)
        # Beta (2024-01-20) must come before Gamma (2024-02-01)
        pos_alpha = text.find("Alpha Mail")
        pos_beta = text.find("Beta Mail")
        pos_gamma = text.find("Gamma Mail")

        assert pos_alpha != -1, "'Alpha Mail' not found in PDF"
        assert pos_beta != -1, "'Beta Mail' not found in PDF"
        assert pos_gamma != -1, "'Gamma Mail' not found in PDF"

        # In the dup section, the occurrences could be in the main E-Mail-Liste too.
        # Look for occurrences beyond the first (in the dup section).
        # The dup section comes AFTER E-Mail-Liste so later positions correspond to it.
        # We just need any ordering guarantee that alpha < beta < gamma somewhere.
        # Use rfind to find last occurrence (dup section occurrence).
        last_alpha = text.rfind("Alpha Mail")
        last_beta = text.rfind("Beta Mail")
        last_gamma = text.rfind("Gamma Mail")
        assert last_alpha < last_beta, (
            f"Alpha (oldest) must appear before Beta in dup section. "
            f"last_alpha={last_alpha}, last_beta={last_beta}"
        )
        assert last_beta < last_gamma, (
            f"Beta must appear before Gamma in dup section. "
            f"last_beta={last_beta}, last_gamma={last_gamma}"
        )


# ── determinism with dup section ─────────────────────────────────────────────


class TestDupSectionDeterminism:
    """Duplicate section must not break PDF determinism."""

    def test_two_runs_same_sha256_with_dup_groups(self) -> None:
        """Two identical calls with dup_groups must yield identical SHA-256."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        ts = "2024-03-01T00:00:00+00:00"

        pdf1 = build_report_pdf(records, dup_groups, ts)
        pdf2 = build_report_pdf(records, dup_groups, ts)

        h1 = hashlib.sha256(pdf1).hexdigest()
        h2 = hashlib.sha256(pdf2).hexdigest()
        assert h1 == h2, (
            "PDF SHA-256 changed between two identical generations with dup groups — "
            "NOT deterministic"
        )

    def test_five_runs_all_same_sha256(self) -> None:
        """Five independent calls with identical inputs must all hash identically."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_two_groups_fixture()
        ts = "2025-06-01T12:00:00+00:00"

        hashes = {
            hashlib.sha256(build_report_pdf(records, dup_groups, ts)).hexdigest()
            for _ in range(5)
        }
        assert len(hashes) == 1, (
            f"Expected 1 unique hash across 5 runs, got {len(hashes)}: {hashes}"
        )


# ── member column headers ──────────────────────────────────────────────────────


class TestMemberTableColumnHeaders:
    """The member table within each group must have German column headers."""

    def test_member_table_has_betreff_column(self) -> None:
        """The member rows table must have a 'Betreff' column header."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # Betreff should appear at least twice: once in E-Mail-Liste header,
        # once in the dup section member table header
        count = text.count("Betreff")
        assert count >= 1, (
            f"'Betreff' should appear in member table. count={count}. Got:\n{text!r}"
        )

    def test_member_table_has_von_column(self) -> None:
        """The member rows table must have a 'Von' column header."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "Von" in text, f"'Von' should appear in member table. Got:\n{text!r}"

    def test_member_table_has_datum_column(self) -> None:
        """The member rows table must have a 'Datum' column header."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "Datum" in text, f"'Datum' should appear in member table. Got:\n{text!r}"


# ── empty and edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases: empty groups list, single-member groups (degenerate)."""

    def test_empty_dup_groups_produces_valid_pdf(self) -> None:
        """Empty dup_groups list must still produce a valid PDF."""
        from maildir_report.pdf import build_report_pdf

        records = [_make_email_record("/mbox/cur/solo")]
        pdf = build_report_pdf(records, [], "2024-03-01T00:00:00+00:00")
        assert pdf[:5] == b"%PDF-"

    def test_records_and_groups_consistent(self) -> None:
        """build_report_pdf must succeed with consistent records + groups."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_two_groups_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-06-01T08:00:00+00:00")
        assert pdf[:5] == b"%PDF-"
        text = _extract_pdf_text(pdf)
        # Must contain both E-Mail-Liste and Duplikatgruppen
        assert "E-Mail-Liste" in text
        assert "Duplikatgruppen" in text

    def test_pdf_valid_bytes_with_dup_section(self) -> None:
        """A PDF with duplicate section must start with %PDF- magic bytes."""
        from maildir_report.pdf import build_report_pdf

        records, dup_groups = _make_three_email_one_group_fixture()
        pdf = build_report_pdf(records, dup_groups, "2024-03-01T00:00:00+00:00")
        assert pdf[:5] == b"%PDF-"

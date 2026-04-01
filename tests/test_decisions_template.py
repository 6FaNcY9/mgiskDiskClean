"""
test_decisions_template.py — TDD tests for Task 1: enriched decisions template generator.

Contracts being tested
----------------------
decisions_template.py
    generate_decisions_template(records) -> list[dict[str, str]]
        - Returns one row dict per email record (no drops, no duplicates).
        - Row dict contains exactly the 14 canonical keys in _HEADERS order.
        - "stable_id" matches the record's "stable_id" field verbatim.
        - "filepath" matches the record's "filepath" field verbatim.
        - "decision" is an empty string (blank field for reviewer input).
        - "folder", "date", "from", "subject" copied from record fields.
        - "total_size_bytes" is a string representation of total_size.
        - "attachment_count" counts all parts in the record.
        - "attachment_total_bytes" sums part sizes.
        - "attachment_names" is semicolon-separated filenames; empty if no parts.
        - "is_duplicate" is "true" when dup_group_id is set, else "false".
        - "dup_group_id" is 64-hex string or empty string.
        - "dup_rank" is integer string or empty string.
        - Row order matches sort_emails(records) — identical to PDF/manifest order.
        - Deterministic: same inputs always produce equal row lists.
        - Empty input returns empty list.

    serialize_decisions_csv(rows) -> str
        - Serialises the row list to a deterministic CSV string.
        - Header row: 14 columns in canonical order.
        - Rows appear in the same order as the input list (no re-sorting).
        - CSV-safe: values with commas/quotes/newlines are properly escaped.
        - Deterministic: same rows always produce the same CSV bytes.

    serialize_decisions_json(rows) -> str
        - Serialises the row list to a deterministic JSON string.
        - JSON array with one object per row, same key order.
        - Deterministic: same rows always produce the same JSON string.
"""

from __future__ import annotations

import csv
import io
import json

import pytest


# ── canonical header list ───────────────────────────────────────────────────

EXPECTED_HEADERS = [
    "stable_id",
    "filepath",
    "decision",
    "folder",
    "date",
    "from",
    "subject",
    "total_size_bytes",
    "attachment_count",
    "attachment_total_bytes",
    "attachment_names",
    "is_duplicate",
    "dup_group_id",
    "dup_rank",
]


# ── helpers ────────────────────────────────────────────────────────────────


def _make_part(filename: str = "doc.pdf", size: int = 1024) -> dict:
    """Return a minimal PartRecord-like dict."""
    return {
        "filename": filename,
        "mime": "application/pdf",
        "size": size,
        "content_hash": "a" * 64,
        "category": "pdf",
        "is_dup": False,
        "dup_group_id": None,
    }


def _make_record(
    filepath: str,
    message_id: str = "",
    stable_id: str = "",
    date: str = "2024-01-01 10:00",
    subject: str = "Test",
    sender: str = "a@example.com",
    folder: str = "INBOX",
    total_size: int = 100,
    parts: list | None = None,
    dup_group_id: str | None = None,
    dup_rank: int | None = None,
) -> dict:
    """Return a minimal EmailRecord-like dict."""
    from maildir_report.ids import email_stable_id

    rec: dict = {
        "filepath": filepath,
        "message_id": message_id,
        "date": date,
        "subject": subject,
        "sender": sender,
        "to": "b@example.com",
        "folder": folder,
        "total_size": total_size,
        "parts": parts if parts is not None else [],
        "dup_group_id": dup_group_id,
        "dup_rank": dup_rank,
    }
    # Use provided stable_id or compute from ids module
    rec["stable_id"] = stable_id if stable_id else email_stable_id(rec)
    return rec


# ── import / API surface ───────────────────────────────────────────────────


class TestDecisionsTemplateImports:
    """All public symbols must be importable from maildir_report.decisions_template."""

    def test_generate_decisions_template_importable(self):
        from maildir_report.decisions_template import generate_decisions_template  # noqa: F401

    def test_serialize_decisions_csv_importable(self):
        from maildir_report.decisions_template import serialize_decisions_csv  # noqa: F401

    def test_serialize_decisions_json_importable(self):
        from maildir_report.decisions_template import serialize_decisions_json  # noqa: F401


# ── generate_decisions_template ────────────────────────────────────────────


class TestGenerateDecisionsTemplate:
    """Core row-generation contracts."""

    def test_empty_input_returns_empty_list(self):
        from maildir_report.decisions_template import generate_decisions_template

        assert generate_decisions_template([]) == []

    def test_row_count_matches_record_count(self):
        from maildir_report.decisions_template import generate_decisions_template

        records = [
            _make_record("/mail/a"),
            _make_record("/mail/b"),
            _make_record("/mail/c"),
        ]
        rows = generate_decisions_template(records)
        assert len(rows) == 3

    def test_no_duplicate_rows(self):
        from maildir_report.decisions_template import generate_decisions_template

        records = [
            _make_record("/mail/a"),
            _make_record("/mail/b"),
            _make_record("/mail/c"),
        ]
        rows = generate_decisions_template(records)
        stable_ids = [r["stable_id"] for r in rows]
        assert len(stable_ids) == len(set(stable_ids))

    def test_row_has_required_keys(self):
        from maildir_report.decisions_template import generate_decisions_template

        rows = generate_decisions_template([_make_record("/mail/a")])
        assert len(rows) == 1
        assert set(rows[0].keys()) == set(EXPECTED_HEADERS)

    def test_row_has_no_extra_keys(self):
        """Rows must have exactly the 14 expected keys — not more, not fewer."""
        from maildir_report.decisions_template import generate_decisions_template

        rows = generate_decisions_template([_make_record("/mail/a")])
        assert len(rows[0]) == 14

    def test_row_keys_in_canonical_order(self):
        """Keys must appear in the canonical _HEADERS order."""
        from maildir_report.decisions_template import generate_decisions_template

        rows = generate_decisions_template([_make_record("/mail/a")])
        assert list(rows[0].keys()) == EXPECTED_HEADERS

    def test_stable_id_matches_record(self):
        from maildir_report.decisions_template import generate_decisions_template
        from maildir_report.ids import email_stable_id

        rec = _make_record("/mail/x", message_id="<x@example.com>")
        rows = generate_decisions_template([rec])
        assert rows[0]["stable_id"] == email_stable_id(rec)

    def test_stable_id_verbatim_from_record_field(self):
        """If the record already has a stable_id set, use it verbatim — do not recompute."""
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/y")
        rec["stable_id"] = "aaaa" * 16  # 64-char synthetic ID
        rows = generate_decisions_template([rec])
        assert rows[0]["stable_id"] == "aaaa" * 16

    def test_filepath_matches_record(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/some/path/file")
        rows = generate_decisions_template([rec])
        assert rows[0]["filepath"] == "/mail/some/path/file"

    def test_decision_is_empty_string(self):
        """The decision field must be an empty string — not None, not 0."""
        from maildir_report.decisions_template import generate_decisions_template

        rows = generate_decisions_template([_make_record("/mail/a")])
        assert rows[0]["decision"] == ""
        assert isinstance(rows[0]["decision"], str)

    def test_decision_is_empty_for_all_rows(self):
        from maildir_report.decisions_template import generate_decisions_template

        records = [_make_record(f"/mail/{i}") for i in range(5)]
        rows = generate_decisions_template(records)
        for row in rows:
            assert row["decision"] == ""


# ── new reviewer context fields ─────────────────────────────────────────────


class TestGenerateDecisionsTemplateContextFields:
    """Reviewer context columns are populated correctly."""

    def test_folder_copied_from_record(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", folder="Sent")
        rows = generate_decisions_template([rec])
        assert rows[0]["folder"] == "Sent"

    def test_date_copied_from_record(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", date="2024-05-15 09:30")
        rows = generate_decisions_template([rec])
        assert rows[0]["date"] == "2024-05-15 09:30"

    def test_from_copied_from_sender(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", sender="alice@example.com")
        rows = generate_decisions_template([rec])
        assert rows[0]["from"] == "alice@example.com"

    def test_subject_copied_from_record(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", subject="Hello World")
        rows = generate_decisions_template([rec])
        assert rows[0]["subject"] == "Hello World"

    def test_total_size_bytes_is_string_of_total_size(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", total_size=4096)
        rows = generate_decisions_template([rec])
        assert rows[0]["total_size_bytes"] == "4096"

    def test_attachment_count_zero_when_no_parts(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", parts=[])
        rows = generate_decisions_template([rec])
        assert rows[0]["attachment_count"] == "0"

    def test_attachment_count_matches_parts_length(self):
        from maildir_report.decisions_template import generate_decisions_template

        parts = [_make_part("a.pdf"), _make_part("b.pdf"), _make_part("c.pdf")]
        rec = _make_record("/mail/a", parts=parts)
        rows = generate_decisions_template([rec])
        assert rows[0]["attachment_count"] == "3"

    def test_attachment_total_bytes_zero_when_no_parts(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", parts=[])
        rows = generate_decisions_template([rec])
        assert rows[0]["attachment_total_bytes"] == "0"

    def test_attachment_total_bytes_sums_part_sizes(self):
        from maildir_report.decisions_template import generate_decisions_template

        parts = [_make_part("a.pdf", size=500), _make_part("b.docx", size=1500)]
        rec = _make_record("/mail/a", parts=parts)
        rows = generate_decisions_template([rec])
        assert rows[0]["attachment_total_bytes"] == "2000"

    def test_attachment_names_empty_when_no_parts(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", parts=[])
        rows = generate_decisions_template([rec])
        assert rows[0]["attachment_names"] == ""

    def test_attachment_names_semicolon_separated(self):
        from maildir_report.decisions_template import generate_decisions_template

        parts = [_make_part("report.pdf"), _make_part("data.xlsx")]
        rec = _make_record("/mail/a", parts=parts)
        rows = generate_decisions_template([rec])
        assert rows[0]["attachment_names"] == "report.pdf;data.xlsx"

    def test_attachment_names_single_file(self):
        from maildir_report.decisions_template import generate_decisions_template

        parts = [_make_part("only.pdf")]
        rec = _make_record("/mail/a", parts=parts)
        rows = generate_decisions_template([rec])
        assert rows[0]["attachment_names"] == "only.pdf"

    def test_is_duplicate_false_when_no_dup_group(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", dup_group_id=None, dup_rank=None)
        rows = generate_decisions_template([rec])
        assert rows[0]["is_duplicate"] == "false"

    def test_is_duplicate_true_when_dup_group_set(self):
        from maildir_report.decisions_template import generate_decisions_template

        gid = "b" * 64
        rec = _make_record("/mail/a", dup_group_id=gid, dup_rank=0)
        rows = generate_decisions_template([rec])
        assert rows[0]["is_duplicate"] == "true"

    def test_dup_group_id_empty_when_not_duplicate(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", dup_group_id=None)
        rows = generate_decisions_template([rec])
        assert rows[0]["dup_group_id"] == ""

    def test_dup_group_id_set_when_duplicate(self):
        from maildir_report.decisions_template import generate_decisions_template

        gid = "c" * 64
        rec = _make_record("/mail/a", dup_group_id=gid, dup_rank=1)
        rows = generate_decisions_template([rec])
        assert rows[0]["dup_group_id"] == gid

    def test_dup_rank_empty_when_not_duplicate(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", dup_rank=None)
        rows = generate_decisions_template([rec])
        assert rows[0]["dup_rank"] == ""

    def test_dup_rank_integer_string_when_set(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", dup_group_id="d" * 64, dup_rank=2)
        rows = generate_decisions_template([rec])
        assert rows[0]["dup_rank"] == "2"

    def test_dup_rank_zero_is_string_zero(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", dup_group_id="e" * 64, dup_rank=0)
        rows = generate_decisions_template([rec])
        assert rows[0]["dup_rank"] == "0"

    def test_subject_short_not_truncated(self):
        from maildir_report.decisions_template import generate_decisions_template

        rec = _make_record("/mail/a", subject="Short subject")
        rows = generate_decisions_template([rec])
        assert rows[0]["subject"] == "Short subject"

    def test_subject_exactly_80_chars_not_truncated(self):
        from maildir_report.decisions_template import generate_decisions_template

        subject_80 = "x" * 80
        rec = _make_record("/mail/a", subject=subject_80)
        rows = generate_decisions_template([rec])
        assert rows[0]["subject"] == subject_80
        assert len(rows[0]["subject"]) == 80

    def test_subject_81_chars_gets_ellipsis(self):
        """Subject longer than 80 chars is truncated to 80 + \u2026 (matches PDF policy)."""
        from maildir_report.decisions_template import generate_decisions_template

        subject_81 = "y" * 81
        rec = _make_record("/mail/a", subject=subject_81)
        rows = generate_decisions_template([rec])
        assert rows[0]["subject"] == "y" * 80 + "\u2026"

    def test_subject_very_long_truncated_to_80_plus_ellipsis(self):
        from maildir_report.decisions_template import generate_decisions_template

        subject_long = "A" * 200
        rec = _make_record("/mail/a", subject=subject_long)
        rows = generate_decisions_template([rec])
        expected = "A" * 80 + "\u2026"
        assert rows[0]["subject"] == expected
        assert len(rows[0]["subject"]) == 81  # 80 chars + 1 ellipsis codepoint


# ── ordering contract ─────────────────────────────────────────────────────


class TestGenerateDecisionsTemplateOrdering:
    """Row order must be consistent with sort_emails(records) — same as PDF/manifest."""

    def test_ordering_matches_sort_emails(self):
        from maildir_report.decisions_template import generate_decisions_template
        from maildir_report.ordering import sort_emails

        records = [
            _make_record("/mail/b", date="2024-03-01 10:00"),
            _make_record("/mail/a", date="2024-01-01 10:00"),
            _make_record("/mail/c", date="2024-02-01 10:00"),
        ]
        rows = generate_decisions_template(records)
        expected_order = [r["filepath"] for r in sort_emails(records)]
        actual_order = [row["filepath"] for row in rows]
        assert actual_order == expected_order

    def test_ordering_stable_by_date_then_filepath(self):
        """Primary sort key: date. Secondary: filepath."""
        from maildir_report.decisions_template import generate_decisions_template

        records = [
            _make_record("/mail/z", date="2024-01-15 09:00"),
            _make_record(
                "/mail/a", date="2024-01-15 09:00"
            ),  # same date, different path
            _make_record("/mail/m", date="2024-01-10 09:00"),
        ]
        rows = generate_decisions_template(records)
        assert rows[0]["filepath"] == "/mail/m"  # earliest date
        assert rows[1]["filepath"] == "/mail/a"  # same date, path /mail/a < /mail/z
        assert rows[2]["filepath"] == "/mail/z"

    def test_input_list_not_mutated(self):
        """generate_decisions_template must not modify the caller's list."""
        from maildir_report.decisions_template import generate_decisions_template

        records = [
            _make_record("/mail/c", date="2024-03-01 10:00"),
            _make_record("/mail/a", date="2024-01-01 10:00"),
        ]
        original_order = [r["filepath"] for r in records]
        generate_decisions_template(records)
        after_order = [r["filepath"] for r in records]
        assert after_order == original_order

    def test_ordering_independent_of_input_order(self):
        """Shuffling the input produces the same row order."""
        import random

        from maildir_report.decisions_template import generate_decisions_template

        records = [
            _make_record(f"/mail/{chr(ord('a') + i)}", date=f"2024-0{i + 1}-01 10:00")
            for i in range(5)
        ]
        expected = generate_decisions_template(records)

        shuffled = list(records)
        random.seed(99)
        random.shuffle(shuffled)
        result = generate_decisions_template(shuffled)

        assert [r["stable_id"] for r in result] == [r["stable_id"] for r in expected]


# ── determinism contract ──────────────────────────────────────────────────


class TestGenerateDecisionsTemplateDeterminism:
    """Same inputs must always produce identical outputs."""

    def test_two_calls_produce_equal_results(self):
        from maildir_report.decisions_template import generate_decisions_template

        records = [
            _make_record("/mail/a", date="2024-01-01 10:00"),
            _make_record("/mail/b", date="2024-02-01 10:00"),
        ]
        rows1 = generate_decisions_template(records)
        rows2 = generate_decisions_template(records)
        assert rows1 == rows2

    def test_result_is_json_serialisable(self):
        """Rows must be JSON-serialisable without conversion (str values only)."""
        from maildir_report.decisions_template import generate_decisions_template

        records = [_make_record("/mail/a"), _make_record("/mail/b")]
        rows = generate_decisions_template(records)
        # Should not raise
        json.dumps(rows)

    def test_all_values_are_strings(self):
        """Every value in every row dict must be a str."""
        from maildir_report.decisions_template import generate_decisions_template

        records = [_make_record("/mail/a"), _make_record("/mail/b")]
        rows = generate_decisions_template(records)
        for row in rows:
            for v in row.values():
                assert isinstance(v, str)


# ── serialize_decisions_csv ───────────────────────────────────────────────


def _make_full_row(
    stable_id: str = "a" * 64,
    filepath: str = "/mail/a",
    folder: str = "INBOX",
    date: str = "2024-01-01 10:00",
    sender: str = "a@example.com",
    subject: str = "Test",
) -> dict:
    """Return a fully-populated row dict matching the 14-column schema."""
    return {
        "stable_id": stable_id,
        "filepath": filepath,
        "decision": "",
        "folder": folder,
        "date": date,
        "from": sender,
        "subject": subject,
        "total_size_bytes": "100",
        "attachment_count": "0",
        "attachment_total_bytes": "0",
        "attachment_names": "",
        "is_duplicate": "false",
        "dup_group_id": "",
        "dup_rank": "",
    }


class TestSerializeDecisionsCsv:
    """CSV serialisation contracts."""

    def test_csv_returns_string(self):
        from maildir_report.decisions_template import serialize_decisions_csv

        result = serialize_decisions_csv([])
        assert isinstance(result, str)

    def test_csv_empty_input_has_header_only(self):
        """Empty row list → CSV with header row only (no data rows)."""
        from maildir_report.decisions_template import serialize_decisions_csv

        result = serialize_decisions_csv([])
        reader = csv.DictReader(io.StringIO(result))
        assert list(reader) == []
        assert reader.fieldnames == EXPECTED_HEADERS

    def test_csv_header_row(self):
        from maildir_report.decisions_template import serialize_decisions_csv

        result = serialize_decisions_csv([])
        first_line = result.splitlines()[0]
        assert first_line == ",".join(EXPECTED_HEADERS)

    def test_csv_row_count_matches_input(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_csv,
        )

        records = [_make_record(f"/mail/{i}") for i in range(4)]
        rows = generate_decisions_template(records)
        csv_str = serialize_decisions_csv(rows)
        reader = csv.DictReader(io.StringIO(csv_str))
        data_rows = list(reader)
        assert len(data_rows) == 4

    def test_csv_stable_id_preserved(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_csv,
        )

        rec = _make_record("/mail/x", message_id="<x@example.com>")
        rows = generate_decisions_template([rec])
        csv_str = serialize_decisions_csv(rows)
        reader = csv.DictReader(io.StringIO(csv_str))
        data_rows = list(reader)
        assert data_rows[0]["stable_id"] == rows[0]["stable_id"]

    def test_csv_filepath_preserved(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_csv,
        )

        rec = _make_record("/mail/some/path")
        rows = generate_decisions_template([rec])
        csv_str = serialize_decisions_csv(rows)
        reader = csv.DictReader(io.StringIO(csv_str))
        data_rows = list(reader)
        assert data_rows[0]["filepath"] == "/mail/some/path"

    def test_csv_decision_is_empty_in_output(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_csv,
        )

        rows = generate_decisions_template([_make_record("/mail/a")])
        csv_str = serialize_decisions_csv(rows)
        reader = csv.DictReader(io.StringIO(csv_str))
        data_rows = list(reader)
        assert data_rows[0]["decision"] == ""

    def test_csv_context_fields_preserved(self):
        """All reviewer context fields must round-trip through CSV correctly."""
        from maildir_report.decisions_template import serialize_decisions_csv

        row = _make_full_row(
            folder="Sent",
            date="2024-03-15 14:30",
            sender="bob@example.com",
            subject="Meeting notes",
        )
        row["total_size_bytes"] = "8192"
        row["attachment_count"] = "2"
        row["attachment_total_bytes"] = "4096"
        row["attachment_names"] = "notes.pdf;agenda.docx"
        row["is_duplicate"] = "true"
        row["dup_group_id"] = "f" * 64
        row["dup_rank"] = "1"
        csv_str = serialize_decisions_csv([row])
        reader = csv.DictReader(io.StringIO(csv_str))
        data_rows = list(reader)
        assert data_rows[0]["folder"] == "Sent"
        assert data_rows[0]["date"] == "2024-03-15 14:30"
        assert data_rows[0]["from"] == "bob@example.com"
        assert data_rows[0]["subject"] == "Meeting notes"
        assert data_rows[0]["total_size_bytes"] == "8192"
        assert data_rows[0]["attachment_count"] == "2"
        assert data_rows[0]["attachment_total_bytes"] == "4096"
        assert data_rows[0]["attachment_names"] == "notes.pdf;agenda.docx"
        assert data_rows[0]["is_duplicate"] == "true"
        assert data_rows[0]["dup_group_id"] == "f" * 64
        assert data_rows[0]["dup_rank"] == "1"

    def test_csv_deterministic(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_csv,
        )

        records = [_make_record("/mail/a"), _make_record("/mail/b")]
        rows = generate_decisions_template(records)
        assert serialize_decisions_csv(rows) == serialize_decisions_csv(rows)

    def test_csv_special_chars_in_filepath_escaped(self):
        """Filepaths with commas must be properly quoted in CSV output."""
        from maildir_report.decisions_template import serialize_decisions_csv

        row = _make_full_row(stable_id="a" * 64, filepath="/mail/foo,bar")
        result = serialize_decisions_csv([row])
        reader = csv.DictReader(io.StringIO(result))
        data_rows = list(reader)
        assert data_rows[0]["filepath"] == "/mail/foo,bar"

    def test_csv_row_order_preserved(self):
        """serialize_decisions_csv must preserve the row order of its input."""
        from maildir_report.decisions_template import serialize_decisions_csv

        rows = [
            _make_full_row(stable_id="a" * 64, filepath="/mail/first"),
            _make_full_row(stable_id="b" * 64, filepath="/mail/second"),
        ]
        csv_str = serialize_decisions_csv(rows)
        reader = csv.DictReader(io.StringIO(csv_str))
        data_rows = list(reader)
        assert data_rows[0]["filepath"] == "/mail/first"
        assert data_rows[1]["filepath"] == "/mail/second"


# ── serialize_decisions_json ──────────────────────────────────────────────


class TestSerializeDecisionsJson:
    """JSON serialisation contracts."""

    def test_json_returns_string(self):
        from maildir_report.decisions_template import serialize_decisions_json

        result = serialize_decisions_json([])
        assert isinstance(result, str)

    def test_json_empty_input_is_empty_array(self):
        from maildir_report.decisions_template import serialize_decisions_json

        result = serialize_decisions_json([])
        parsed = json.loads(result)
        assert parsed == []

    def test_json_row_count_matches_input(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_json,
        )

        records = [_make_record(f"/mail/{i}") for i in range(3)]
        rows = generate_decisions_template(records)
        parsed = json.loads(serialize_decisions_json(rows))
        assert len(parsed) == 3

    def test_json_stable_id_preserved(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_json,
        )

        rec = _make_record("/mail/j", message_id="<j@example.com>")
        rows = generate_decisions_template([rec])
        parsed = json.loads(serialize_decisions_json(rows))
        assert parsed[0]["stable_id"] == rows[0]["stable_id"]

    def test_json_filepath_preserved(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_json,
        )

        rec = _make_record("/mail/json/path")
        rows = generate_decisions_template([rec])
        parsed = json.loads(serialize_decisions_json(rows))
        assert parsed[0]["filepath"] == "/mail/json/path"

    def test_json_decision_is_empty_string(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_json,
        )

        rows = generate_decisions_template([_make_record("/mail/a")])
        parsed = json.loads(serialize_decisions_json(rows))
        assert parsed[0]["decision"] == ""

    def test_json_object_keys_are_all_14_headers(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_json,
        )

        rows = generate_decisions_template([_make_record("/mail/a")])
        parsed = json.loads(serialize_decisions_json(rows))
        assert set(parsed[0].keys()) == set(EXPECTED_HEADERS)

    def test_json_deterministic(self):
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_json,
        )

        records = [_make_record("/mail/a"), _make_record("/mail/b")]
        rows = generate_decisions_template(records)
        assert serialize_decisions_json(rows) == serialize_decisions_json(rows)

    def test_json_roundtrip_equal_to_rows(self):
        """JSON round-trip must reproduce the original rows exactly."""
        from maildir_report.decisions_template import (
            generate_decisions_template,
            serialize_decisions_json,
        )

        records = [_make_record(f"/mail/{i}") for i in range(4)]
        rows = generate_decisions_template(records)
        parsed = json.loads(serialize_decisions_json(rows))
        assert parsed == rows

    def test_json_row_order_preserved(self):
        """serialize_decisions_json must preserve the row order of its input."""
        from maildir_report.decisions_template import serialize_decisions_json

        rows = [
            _make_full_row(stable_id="a" * 64, filepath="/mail/first"),
            _make_full_row(stable_id="b" * 64, filepath="/mail/second"),
        ]
        parsed = json.loads(serialize_decisions_json(rows))
        assert parsed[0]["filepath"] == "/mail/first"
        assert parsed[1]["filepath"] == "/mail/second"

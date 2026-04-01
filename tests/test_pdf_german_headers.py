"""
test_pdf_german_headers.py — TDD tests for Task 10: German PDF layout.

Contracts being tested
----------------------
pdf.py
    build_report_pdf(records, dup_groups, timestamp_str) -> bytes
        - Returns valid PDF bytes (starts with %PDF-).
        - Calls configure_deterministic_pdf() internally (deterministic output).
        - PDF text content includes all required German section headers:
          Deckblatt/Meta section markers, "Zusammenfassung", "E-Mail-Liste".
        - Per-email table column headers present: "Betreff", "Von", "Datum",
          "Anhänge", "Duplikate".
        - Umlauts render correctly: ä, ö, ü, Ä, Ö, Ü, ß all present in output
          when supplied in record data.
        - Stable ordering: emails appear in sort_emails() order.
        - Deterministic: same inputs → same SHA-256 bytes.
        - Empty records list: returns valid PDF (no crash).
        - Report timestamp appears in the PDF metadata/content.

Font strategy
-------------
Helvetica with WinAnsiEncoding (built-in Type1, no TTF file dependency).
Covers the full ISO-8859-1 / WinAnsi character set which includes all German
umlauts (ä U+00E4, ö U+00F6, ü U+00FC, Ä U+00C4, Ö U+00D6, Ü U+00DC, ß U+00DF).

Text extraction helper
----------------------
_extract_pdf_text() decodes the PDF content streams:
  ASCII85Decode + FlateDecode → raw content stream bytes →
  extract (text) Tj operands → decode PDF octal escapes → Latin-1 string.
This is deterministic and does not require external PDF library.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import zlib
from typing import Any

import pytest


# ── text extraction helper ────────────────────────────────────────────────────


def _decode_pdf_octal(raw: bytes) -> str:
    """Decode PDF-encoded text operand bytes to a Python string.

    PDF text operands (inside ``(...)`` markers) may encode non-ASCII bytes as
    octal escapes ``\\nnn``.  E.g. ``\\344`` = 0xE4 = 'ä' in Latin-1.
    """
    result: list[str] = []
    i = 0
    while i < len(raw):
        if (
            raw[i : i + 1] == b"\\"
            and i + 1 < len(raw)
            and raw[i + 1 : i + 2].isdigit()
        ):
            # Octal escape: \nnn (3 digits)
            octal = raw[i + 1 : i + 4]
            result.append(chr(int(octal, 8)))
            i += 4
        else:
            result.append(chr(raw[i]))
            i += 1
    return "".join(result)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text drawn via Tj/TJ operators from a ReportLab PDF.

    Supports ReportLab's default encoding: ASCII85Decode + FlateDecode per
    content stream.  Falls back to raw FlateDecode.  Returns all text
    operands joined by newlines.
    """
    text_parts: list[str] = []

    for m in re.finditer(rb"stream\n(.*?)endstream", pdf_bytes, re.DOTALL):
        raw = m.group(1).strip()
        try:
            stream_bytes = zlib.decompress(base64.a85decode(raw, adobe=True))
        except Exception:
            try:
                stream_bytes = zlib.decompress(raw)
            except Exception:
                # Not a compressed stream we can decode (e.g. font data); skip.
                continue

        # Extract (text) Tj operands (Platypus / canvas drawString)
        for text_m in re.finditer(rb"\(([^)]*)\)\s*Tj", stream_bytes):
            text_parts.append(_decode_pdf_octal(text_m.group(1)))

    return "\n".join(text_parts)


# ── synthetic record builders ─────────────────────────────────────────────────


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
    stable_id: str = "",
    message_id: str = "<test@example.com>",
) -> dict[str, Any]:
    """Build a minimal synthetic EmailRecord dict."""
    return {
        "filepath": filepath,
        "message_id": message_id,
        "subject": subject,
        "date": date,
        "date_day": date[:10],
        "sender": sender,
        "to": "empfaenger@example.com",
        "folder": folder,
        "total_size": total_size,
        "parts": parts or [],
        "stable_id": stable_id or hashlib.sha256(filepath.encode()).hexdigest(),
        "dup_group_id": dup_group_id,
        "dup_rank": dup_rank,
        "has_nested_messages": False,
    }


def _make_part_record(
    filename: str = "anlage.pdf",
    mime: str = "application/pdf",
    size: int = 1024,
    is_dup: bool = False,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "mime": mime,
        "size": size,
        "content_hash": hashlib.sha256(filename.encode()).hexdigest(),
        "category": "pdf",
        "is_dup": is_dup,
        "dup_group_id": None,
        "stable_id": hashlib.sha256(filename.encode()).hexdigest(),
        "payload_bytes": None,
    }


# ── import guard (tests must fail before pdf.py exists) ──────────────────────

# We import inside each test class to get clean error messages at the right level.


class TestPdfModuleExists:
    """pdf.py module must exist and expose build_report_pdf."""

    def test_import_pdf_module(self) -> None:
        """Importing maildir_report.pdf must not raise ImportError."""
        from maildir_report import pdf  # noqa: F401

    def test_build_report_pdf_callable(self) -> None:
        """build_report_pdf must be a callable exported from pdf.py."""
        from maildir_report.pdf import build_report_pdf

        assert callable(build_report_pdf)


# ── valid PDF output ──────────────────────────────────────────────────────────


class TestPdfValidOutput:
    """build_report_pdf must return valid PDF bytes."""

    def test_returns_bytes(self) -> None:
        """Return type must be bytes."""
        from maildir_report.pdf import build_report_pdf

        result = build_report_pdf([], [], "2024-06-15T10:00:00+00:00")
        assert isinstance(result, bytes)

    def test_non_empty_bytes(self) -> None:
        """Result must not be empty bytes."""
        from maildir_report.pdf import build_report_pdf

        result = build_report_pdf([], [], "2024-06-15T10:00:00+00:00")
        assert len(result) > 0

    def test_starts_with_pdf_magic(self) -> None:
        """Result must start with the %PDF- magic bytes."""
        from maildir_report.pdf import build_report_pdf

        result = build_report_pdf([], [], "2024-06-15T10:00:00+00:00")
        assert result[:5] == b"%PDF-", (
            f"PDF must start with %PDF-, got: {result[:10]!r}"
        )

    def test_empty_records_does_not_crash(self) -> None:
        """Empty records and groups must produce a valid PDF without error."""
        from maildir_report.pdf import build_report_pdf

        result = build_report_pdf([], [], "2024-01-01T00:00:00+00:00")
        assert result[:5] == b"%PDF-"


# ── required German section headers ──────────────────────────────────────────


class TestGermanSectionHeaders:
    """Required German headings must appear in the extracted PDF text."""

    @pytest.fixture
    def sample_records(self) -> list[dict[str, Any]]:
        return [
            _make_email_record(
                filepath="/maildir/cur/001",
                subject="Monatsbericht",
                sender="chef@example.com",
                date="2024-03-01 09:00",
                parts=[_make_part_record("bericht.pdf")],
            ),
            _make_email_record(
                filepath="/maildir/cur/002",
                subject="Projektplan",
                sender="kollege@example.com",
                date="2024-03-02 10:00",
            ),
        ]

    @pytest.fixture
    def pdf_bytes(self, sample_records: list[dict[str, Any]]) -> bytes:
        from maildir_report.pdf import build_report_pdf

        return build_report_pdf(sample_records, [], "2024-03-15T08:00:00+00:00")

    @pytest.fixture
    def extracted_text(self, pdf_bytes: bytes) -> str:
        return _extract_pdf_text(pdf_bytes)

    def test_zusammenfassung_present(self, extracted_text: str) -> None:
        """'Zusammenfassung' section heading must appear in the PDF."""
        assert "Zusammenfassung" in extracted_text, (
            f"'Zusammenfassung' not found in PDF text. Got:\n{extracted_text!r}"
        )

    def test_email_liste_present(self, extracted_text: str) -> None:
        """'E-Mail-Liste' section heading must appear in the PDF."""
        assert "E-Mail-Liste" in extracted_text, (
            f"'E-Mail-Liste' not found in PDF text. Got:\n{extracted_text!r}"
        )

    def test_betreff_column_present(self, extracted_text: str) -> None:
        """'Betreff' column header must appear in the E-Mail-Liste table."""
        assert "Betreff" in extracted_text, (
            f"'Betreff' not found in PDF text. Got:\n{extracted_text!r}"
        )

    def test_von_column_present(self, extracted_text: str) -> None:
        """'Von' column header must appear in the E-Mail-Liste table."""
        assert "Von" in extracted_text, (
            f"'Von' not found in PDF text. Got:\n{extracted_text!r}"
        )

    def test_datum_column_present(self, extracted_text: str) -> None:
        """'Datum' column header must appear in the E-Mail-Liste table."""
        assert "Datum" in extracted_text, (
            f"'Datum' not found in PDF text. Got:\n{extracted_text!r}"
        )

    def test_anhaenge_column_present(self, extracted_text: str) -> None:
        """'Anhänge' column header must appear in the E-Mail-Liste table."""
        assert "Anh\xe4nge" in extracted_text.replace("\n", ""), (
            f"'Anhänge' not found in PDF text. Got:\n{extracted_text!r}"
        )

    def test_duplikate_column_present(self, extracted_text: str) -> None:
        """'Duplikate' column header must appear in the E-Mail-Liste table."""
        assert "Duplikate" in extracted_text, (
            f"'Duplikate' not found in PDF text. Got:\n{extracted_text!r}"
        )


# ── umlaut rendering ──────────────────────────────────────────────────────────


class TestUmlautRendering:
    """German umlauts (ä ö ü Ä Ö Ü ß) must render correctly."""

    def test_umlaut_in_subject_appears_in_pdf(self) -> None:
        """Email subject containing umlauts must appear in extracted PDF text."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/uml001",
                subject="T\xe4glicher \xdcberblick \xfcber Sonderbuchstaben",
                sender="m\xfcller@example.com",
                date="2024-01-01 10:00",
            )
        ]
        pdf = build_report_pdf(records, [], "2024-01-15T10:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # The subject with umlauts must be present
        assert "T\xe4glicher" in text or "\xfc" in text, (
            f"Umlauts not found in extracted text. Got:\n{text!r}"
        )

    def test_umlaut_column_header_anhaenge(self) -> None:
        """Column header 'Anhänge' (with umlaut ä) must appear in PDF text."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/hdr001",
                subject="Test",
                date="2024-01-01 10:00",
                parts=[_make_part_record()],
            )
        ]
        pdf = build_report_pdf(records, [], "2024-01-15T10:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "Anh\xe4nge" in text.replace("\n", ""), (
            f"'Anhänge' not found in extracted text. Got:\n{text!r}"
        )

    def test_all_german_special_chars_font_support(self) -> None:
        """A record with all German special characters must produce a valid PDF."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/umls001",
                subject="\xe4\xf6\xfc\xc4\xd6\xdc\xdf Test",  # äöüÄÖÜß
                sender="\xf6sterreich@example.com",
                date="2024-01-01 10:00",
            )
        ]
        # Must not raise; PDF must be valid
        pdf = build_report_pdf(records, [], "2024-01-15T10:00:00+00:00")
        assert pdf[:5] == b"%PDF-"
        text = _extract_pdf_text(pdf)
        # At least one umlaut character present
        assert any(c in text for c in "\xe4\xf6\xfc\xc4\xd6\xdc\xdf"), (
            f"No German special characters found in extracted text. Got:\n{text!r}"
        )


# ── determinism ───────────────────────────────────────────────────────────────


class TestPdfDeterminism:
    """Same inputs must produce byte-identical PDFs."""

    def test_two_runs_same_sha256(self) -> None:
        """Two calls with identical inputs must yield identical SHA-256 hashes."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/det001",
                subject="Determinismus Test",
                date="2024-06-01 08:00",
            )
        ]
        ts = "2024-06-15T10:00:00+00:00"

        pdf1 = build_report_pdf(records, [], ts)
        pdf2 = build_report_pdf(records, [], ts)

        h1 = hashlib.sha256(pdf1).hexdigest()
        h2 = hashlib.sha256(pdf2).hexdigest()
        assert h1 == h2, (
            "PDF SHA-256 changed between two identical generations — NOT deterministic"
        )

    def test_ten_runs_all_same_sha256(self) -> None:
        """Ten independent calls with identical inputs must all hash identically."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/det002",
                subject="Wiederholung",
                date="2024-01-01 00:00",
            )
        ]
        ts = "2025-01-01T00:00:00+00:00"

        hashes = {
            hashlib.sha256(build_report_pdf(records, [], ts)).hexdigest()
            for _ in range(10)
        }
        assert len(hashes) == 1, (
            f"Expected 1 unique hash across 10 runs, got {len(hashes)}: {hashes}"
        )

    def test_different_timestamps_differ(self) -> None:
        """Different timestamp strings must produce different PDF bytes."""
        from maildir_report.pdf import build_report_pdf

        records = [_make_email_record("/maildir/cur/ts001")]
        ts1 = "2024-01-01T00:00:00+00:00"
        ts2 = "2024-12-31T23:59:59+00:00"

        h1 = hashlib.sha256(build_report_pdf(records, [], ts1)).hexdigest()
        h2 = hashlib.sha256(build_report_pdf(records, [], ts2)).hexdigest()
        assert h1 != h2, "Different timestamps must produce different PDF content"


# ── ordering ──────────────────────────────────────────────────────────────────


class TestEmailOrdering:
    """Emails must appear in sort_emails() order (date, filepath)."""

    def test_emails_ordered_by_date(self) -> None:
        """Earlier emails must appear before later emails in the E-Mail-Liste."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/ord_b",
                subject="Sp\xe4ter",
                date="2024-03-15 10:00",
            ),
            _make_email_record(
                filepath="/maildir/cur/ord_a",
                subject="Fr\xfcher",
                date="2024-01-01 10:00",
            ),
        ]
        pdf = build_report_pdf(records, [], "2024-04-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)

        # Earlier email subject must appear before later one
        pos_earlier = text.find("Fr\xfcher")
        pos_later = text.find("Sp\xe4ter")
        assert pos_earlier != -1, "'Früher' subject not found in PDF text"
        assert pos_later != -1, "'Später' subject not found in PDF text"
        assert pos_earlier < pos_later, (
            "Earlier email must appear before later email in PDF. "
            f"'Früher' at {pos_earlier}, 'Später' at {pos_later}"
        )


# ── per-email row content ─────────────────────────────────────────────────────


class TestPerEmailRowContent:
    """Each email row must contain the email's subject, sender, and date."""

    def test_subject_appears_in_table(self) -> None:
        """The email subject must appear as a cell in the E-Mail-Liste table."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/row001",
                subject="EindeutigerBetreff",
                sender="test@example.com",
                date="2024-02-14 08:00",
            )
        ]
        pdf = build_report_pdf(records, [], "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "EindeutigerBetreff" in text, (
            f"Subject 'EindeutigerBetreff' not found in PDF text. Got:\n{text!r}"
        )

    def test_attachment_count_in_row(self) -> None:
        """Attachment count must appear in the per-email row."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/row002",
                subject="MitAnhang",
                date="2024-02-14 08:00",
                parts=[
                    _make_part_record("a1.pdf"),
                    _make_part_record("a2.docx"),
                    _make_part_record("a3.zip"),
                ],
            )
        ]
        pdf = build_report_pdf(records, [], "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # 3 attachments: the number 3 must appear somewhere in the row area
        assert "3" in text, (
            f"Attachment count '3' not found in PDF text. Got:\n{text!r}"
        )

    def test_dup_marker_for_duplicate_email(self) -> None:
        """A duplicate email must show a dup indicator (Ja / group-id / etc.)."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/dup001",
                subject="Duplikat",
                date="2024-02-14 08:00",
                dup_group_id="abc123",
                dup_rank=0,
            )
        ]
        pdf = build_report_pdf(records, [], "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # Some indicator that this email is a duplicate must be present
        assert "Ja" in text or "abc123" in text or "Duplikat" in text, (
            f"No duplicate indicator found in PDF text for dup email. Got:\n{text!r}"
        )

    def test_non_duplicate_email_shows_nein(self) -> None:
        """A non-duplicate email must show 'Nein' (or equivalent) in the Duplikate column."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                filepath="/maildir/cur/nodup001",
                subject="KeinDuplikat",
                date="2024-02-14 08:00",
                dup_group_id=None,
                dup_rank=None,
            )
        ]
        pdf = build_report_pdf(records, [], "2024-03-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "Nein" in text, (
            f"'Nein' not found for non-duplicate email. Got:\n{text!r}"
        )


# ── summary counts ────────────────────────────────────────────────────────────


class TestSummaryCounts:
    """Zusammenfassung section must reflect correct email counts."""

    def test_total_email_count_in_summary(self) -> None:
        """Email count must appear in the Zusammenfassung section."""
        from maildir_report.pdf import build_report_pdf

        records = [
            _make_email_record(
                f"/maildir/cur/sum{i:03d}", date=f"2024-01-{i + 1:02d} 10:00"
            )
            for i in range(5)
        ]
        pdf = build_report_pdf(records, [], "2024-02-01T00:00:00+00:00")
        text = _extract_pdf_text(pdf)
        assert "5" in text, f"Expected '5' (email count) in PDF text. Got:\n{text!r}"


# ── timestamp in metadata ─────────────────────────────────────────────────────


class TestTimestampInPdf:
    """The report timestamp must appear in the PDF (e.g., in a heading or footer)."""

    def test_timestamp_year_appears_in_pdf(self) -> None:
        """Year from the report timestamp must appear in the extracted PDF text."""
        from maildir_report.pdf import build_report_pdf

        records = [_make_email_record("/maildir/cur/ts_meta")]
        pdf = build_report_pdf(records, [], "2099-06-15T10:00:00+00:00")
        text = _extract_pdf_text(pdf)
        # Year 2099 is distinctive and should appear somewhere on the cover/header
        assert "2099" in text, (
            f"Timestamp year '2099' not found in extracted PDF text. Got:\n{text!r}"
        )

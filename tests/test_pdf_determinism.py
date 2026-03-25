"""
test_pdf_determinism.py — TDD tests for Task 9: deterministic timestamp + PDF
metadata strategy.

Contracts being tested
----------------------
runtime.py
    parse_report_timestamp(ts_str) -> datetime (UTC, timezone-aware)
        - Accepts ISO 8601 strings with or without timezone offset.
        - Returns a timezone-aware datetime fixed to the input value.
        - Raises ValueError on unparseable input.
        - Never calls datetime.now() — callers must supply the timestamp.

pdf_determinism.py
    configure_deterministic_pdf() -> None
        - Sets rl_config.invariant = True before any PDF generation.
        - Idempotent: calling it twice is safe.

Integration: same input + same fixed timestamp => identical PDF SHA-256 bytes.
    - Two PDF generations from the same in-memory content produce byte-identical
      output when configure_deterministic_pdf() is called first.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from maildir_report.runtime import parse_report_timestamp
from maildir_report.pdf_determinism import configure_deterministic_pdf


# ── parse_report_timestamp: basic contract ────────────────────────────────────


class TestParseReportTimestamp:
    """parse_report_timestamp converts an ISO 8601 string to a tz-aware datetime."""

    def test_parse_iso_with_utc_offset_zero(self) -> None:
        """'2024-01-15T10:30:00+00:00' must parse to a UTC-aware datetime."""
        dt = parse_report_timestamp("2024-01-15T10:30:00+00:00")
        assert isinstance(dt, datetime)
        assert dt.tzinfo is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15
        assert dt.hour == 10
        assert dt.minute == 30

    def test_parse_iso_with_positive_offset(self) -> None:
        """'+02:00' offset is preserved (returned as UTC-normalised)."""
        dt = parse_report_timestamp("2024-06-01T12:00:00+02:00")
        assert dt.tzinfo is not None
        # Normalised to UTC: 12:00 +02:00 = 10:00 UTC
        assert dt.hour == 10
        assert dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_parse_iso_with_negative_offset(self) -> None:
        """'-05:00' offset is normalised to UTC correctly."""
        dt = parse_report_timestamp("2024-12-31T20:00:00-05:00")
        assert dt.tzinfo is not None
        # 20:00 - (-5h) = 01:00 next day UTC
        assert dt.day == 1
        assert dt.month == 1
        assert dt.year == 2025
        assert dt.hour == 1

    def test_parse_iso_naive_string_treated_as_utc(self) -> None:
        """A naive ISO 8601 string without timezone is treated as UTC."""
        dt = parse_report_timestamp("2024-03-20T08:00:00")
        assert dt.tzinfo is not None
        assert dt == datetime(2024, 3, 20, 8, 0, 0, tzinfo=timezone.utc)

    def test_parse_iso_compact_format(self) -> None:
        """'2024-03-20T08:00:00Z' (Z suffix) parses as UTC."""
        dt = parse_report_timestamp("2024-03-20T08:00:00Z")
        assert dt.tzinfo is not None
        assert dt.year == 2024

    def test_parse_invalid_string_raises_value_error(self) -> None:
        """Completely unparseable string must raise ValueError."""
        with pytest.raises(ValueError):
            parse_report_timestamp("not-a-timestamp")

    def test_parse_empty_string_raises_value_error(self) -> None:
        """Empty string must raise ValueError (not silently return garbage)."""
        with pytest.raises(ValueError):
            parse_report_timestamp("")

    def test_parse_date_only_string_raises_value_error(self) -> None:
        """Date-only '2024-03-20' (no time component) must raise ValueError."""
        with pytest.raises(ValueError):
            parse_report_timestamp("2024-03-20")

    def test_result_is_always_utc(self) -> None:
        """The returned datetime must always have UTC tzinfo (offset 0)."""
        for ts in [
            "2024-01-01T00:00:00+00:00",
            "2024-07-04T12:00:00-04:00",
            "2023-12-25T00:00:00+05:30",
        ]:
            dt = parse_report_timestamp(ts)
            assert dt.utcoffset().total_seconds() == 0, (  # type: ignore[union-attr]
                f"Expected UTC offset 0 for {ts!r}, got {dt.utcoffset()!r}"
            )

    def test_iso_string_round_trip(self) -> None:
        """parse then re-format to ISO must yield a consistent string."""
        ts_in = "2024-09-15T14:30:00+00:00"
        dt = parse_report_timestamp(ts_in)
        ts_out = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        assert ts_out == ts_in

    def test_same_timestamp_same_result(self) -> None:
        """Same input string always produces an equal datetime (deterministic)."""
        ts = "2025-01-01T00:00:00+00:00"
        results = [parse_report_timestamp(ts) for _ in range(10)]
        assert all(r == results[0] for r in results), (
            "Non-deterministic timestamp parsing"
        )


# ── configure_deterministic_pdf: basic contract ───────────────────────────────


class TestConfigureDeterministicPdf:
    """configure_deterministic_pdf sets rl_config.invariant = True."""

    def test_configure_sets_invariant_true(self) -> None:
        """After calling configure_deterministic_pdf(), rl_config.invariant must be True."""
        from reportlab import rl_config

        configure_deterministic_pdf()
        assert rl_config.invariant is True

    def test_configure_idempotent(self) -> None:
        """Calling configure_deterministic_pdf() twice must not raise or reset the flag."""
        from reportlab import rl_config

        configure_deterministic_pdf()
        configure_deterministic_pdf()
        assert rl_config.invariant is True

    def test_configure_returns_none(self) -> None:
        """configure_deterministic_pdf() must return None (no side channel)."""
        result = configure_deterministic_pdf()
        assert result is None


# ── PDF byte-identical determinism: integration test ─────────────────────────


def _generate_minimal_pdf(title: str, body: str, timestamp: datetime) -> bytes:
    """Generate a minimal in-memory PDF using ReportLab and return raw bytes.

    This helper is intentionally minimal: title + body + timestamp in metadata.
    The important invariant is that the same (title, body, timestamp) triple
    always produces identical raw bytes after configure_deterministic_pdf().
    """
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(title)
    c.setAuthor("maildir_report")
    c.setSubject(f"Report generated: {timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    c.setCreator("maildir_report/0.1.0")
    c.drawString(72, 700, title)
    c.drawString(72, 680, body)
    c.drawString(72, 660, f"Timestamp: {timestamp.isoformat()}")
    c.showPage()
    c.save()
    return buf.getvalue()


class TestPdfByteDeterminism:
    """Same content + same fixed timestamp must produce identical PDF bytes."""

    def test_two_runs_same_sha256(self) -> None:
        """Generate the same minimal PDF twice; hashes must be identical."""
        configure_deterministic_pdf()
        ts = parse_report_timestamp("2024-06-15T10:00:00+00:00")

        pdf1 = _generate_minimal_pdf("Test Report", "Hello determinism", ts)
        pdf2 = _generate_minimal_pdf("Test Report", "Hello determinism", ts)

        hash1 = hashlib.sha256(pdf1).hexdigest()
        hash2 = hashlib.sha256(pdf2).hexdigest()
        assert hash1 == hash2, (
            "PDF SHA-256 changed between two identical generations — NOT deterministic"
        )

    def test_different_timestamps_produce_different_pdfs(self) -> None:
        """Different timestamps must produce different PDF bytes (content differs)."""
        configure_deterministic_pdf()
        ts1 = parse_report_timestamp("2024-01-01T00:00:00+00:00")
        ts2 = parse_report_timestamp("2024-12-31T23:59:59+00:00")

        pdf1 = _generate_minimal_pdf("Report", "Same body", ts1)
        pdf2 = _generate_minimal_pdf("Report", "Same body", ts2)

        assert hashlib.sha256(pdf1).hexdigest() != hashlib.sha256(pdf2).hexdigest(), (
            "Different timestamps should produce different PDF content"
        )

    def test_different_content_produces_different_pdfs(self) -> None:
        """Different body content must produce different PDF bytes."""
        configure_deterministic_pdf()
        ts = parse_report_timestamp("2024-06-15T10:00:00+00:00")

        pdf1 = _generate_minimal_pdf("Report", "Body A", ts)
        pdf2 = _generate_minimal_pdf("Report", "Body B", ts)

        assert hashlib.sha256(pdf1).hexdigest() != hashlib.sha256(pdf2).hexdigest(), (
            "Different content bodies should produce different PDFs"
        )

    def test_many_runs_all_same_sha256(self) -> None:
        """Ten independent PDF generations with identical inputs must all have the same hash."""
        configure_deterministic_pdf()
        ts = parse_report_timestamp("2025-03-01T08:00:00+00:00")

        hashes = {
            hashlib.sha256(
                _generate_minimal_pdf("Batch Test", f"Iteration constant", ts)
            ).hexdigest()
            for _ in range(10)
        }
        assert len(hashes) == 1, (
            f"Expected one unique hash across 10 runs, got {len(hashes)}: {hashes}"
        )

    def test_pdf_bytes_are_nonempty(self) -> None:
        """The generated PDF must not be empty bytes."""
        configure_deterministic_pdf()
        ts = parse_report_timestamp("2024-01-01T12:00:00+00:00")
        pdf = _generate_minimal_pdf("Non-empty check", "body", ts)
        assert len(pdf) > 0, "PDF must not be empty bytes"

    def test_pdf_starts_with_pdf_signature(self) -> None:
        """A valid PDF must begin with the %PDF- magic bytes."""
        configure_deterministic_pdf()
        ts = parse_report_timestamp("2024-01-01T12:00:00+00:00")
        pdf = _generate_minimal_pdf("Signature check", "body", ts)
        assert pdf[:5] == b"%PDF-", (
            f"PDF does not start with %PDF- signature: {pdf[:10]!r}"
        )

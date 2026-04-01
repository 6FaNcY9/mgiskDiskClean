"""
pdf.py — Deterministic German PDF report renderer for maildir_report.

Design rules
------------
- Call configure_deterministic_pdf() at the top of build_report_pdf() before
  creating any ReportLab document.  This ensures byte-for-byte reproducibility.
- NO datetime.now() calls.  The report timestamp is always supplied by the caller
  as an ISO 8601 string and parsed via runtime.parse_report_timestamp().
- All list iteration uses sort_emails() and sort_dup_groups() for stable order.
- Font: Helvetica (built-in Type1) with WinAnsiEncoding.  This covers the full
  ISO-8859-1 / WinAnsi set, which includes all German characters:
    ä U+00E4, ö U+00F6, ü U+00FC, Ä U+00C4, Ö U+00D6, Ü U+00DC, ß U+00DF.
  No external TTF files are required — the font is embedded by ReportLab
  automatically as a standard Type1 font.

PDF structure (German section labels)
--------------------------------------
1. Deckblatt / Meta
   - Report title: "Maildir-Bericht"
   - Generated-at timestamp
   - Total email count
2. Zusammenfassung
   - Gesamt: N E-Mails
   - Gesamt-Größe: X bytes
   - Duplikatgruppen: N
3. E-Mail-Liste
   Table columns: Betreff | Von | Datum | Anhänge | Duplikate

Public API
----------
build_report_pdf(
    records: list[EmailRecord],
    dup_groups: list[DupGroupRecord],
    timestamp_str: str,
) -> bytes
    Render a deterministic German PDF and return raw bytes.

    Parameters
    ----------
    records:
        List of EmailRecord dicts as produced by parser + group_emails().
        Each record must carry the standard fields defined in models.py.
    dup_groups:
        List of DupGroupRecord dicts as returned by dedup.group_emails().
        May be empty if no duplicates exist.
    timestamp_str:
        ISO 8601 string for the "generated at" timestamp.  Parsed via
        runtime.parse_report_timestamp(); raises ValueError on bad input.
        Must contain a time component (date-only strings are rejected).

    Returns
    -------
    bytes
        Raw PDF bytes.  The same (records, dup_groups, timestamp_str) triple
        always produces byte-identical output when configure_deterministic_pdf()
        has been called (it IS called internally before every build).
"""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from maildir_report.ordering import sort_dup_groups, sort_emails
from maildir_report.pdf_determinism import configure_deterministic_pdf
from maildir_report.runtime import format_report_timestamp, parse_report_timestamp

# ── HTML-escape helper (Paragraph parses XML internally) ─────────────────────

def _esc(text: str) -> str:
    """Escape & < > so ReportLab's Paragraph XML parser doesn't choke on them."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
# ── Page layout constants ─────────────────────────────────────────────────────

_LEFT_MARGIN = 20 * mm
_RIGHT_MARGIN = 20 * mm
_TOP_MARGIN = 20 * mm
_BOTTOM_MARGIN = 20 * mm

# ── Style definitions ─────────────────────────────────────────────────────────


def _make_styles() -> dict[str, ParagraphStyle]:
    """Return a dict of named ParagraphStyle objects for the report."""
    base = getSampleStyleSheet()

    title = ParagraphStyle(
        "GermanTitle",
        fontName="Helvetica-Bold",
        fontSize=18,
        spaceAfter=6,
        parent=base["Normal"],
    )
    heading1 = ParagraphStyle(
        "GermanH1",
        fontName="Helvetica-Bold",
        fontSize=13,
        spaceBefore=12,
        spaceAfter=4,
        parent=base["Normal"],
    )
    meta = ParagraphStyle(
        "GermanMeta",
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.HexColor("#555555"),
        spaceAfter=2,
        parent=base["Normal"],
    )
    body = ParagraphStyle(
        "GermanBody",
        fontName="Helvetica",
        fontSize=10,
        spaceAfter=3,
        parent=base["Normal"],
    )

    return {
        "title": title,
        "heading1": heading1,
        "meta": meta,
        "body": body,
    }


# ── Table style ───────────────────────────────────────────────────────────────


def _email_list_table_style() -> TableStyle:
    """Return the TableStyle for the E-Mail-Liste table."""
    return TableStyle(
        [
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#404040")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
            ("TOPPADDING", (0, 0), (-1, 0), 4),
            # Data rows
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
            ("TOPPADDING", (0, 1), (-1, -1), 3),
            # Alternating row shading
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [colors.white, colors.HexColor("#F5F5F5")],
            ),
            # Grid
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (3, 0), (4, -1), "CENTER"),  # Anhänge + Duplikate centred
        ]
    )


def _dup_member_table_style() -> TableStyle:
    """Return the TableStyle for the per-group member table in the dup section."""
    return TableStyle(
        [
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5B5EA6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
            ("TOPPADDING", (0, 0), (-1, 0), 3),
            # Data rows
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
            ("TOPPADDING", (0, 1), (-1, -1), 2),
            # Alternating row shading
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [colors.white, colors.HexColor("#EEF0FF")],
            ),
            # Grid
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
    )

# ── Section builders ──────────────────────────────────────────────────────────


def _build_deckblatt(
    styles: dict[str, ParagraphStyle],
    timestamp_formatted: str,
    total_emails: int,
) -> list[Any]:
    """Build the Deckblatt/Meta section flowables."""
    return [
        Paragraph("Maildir-Bericht", styles["title"]),
        Paragraph(f"Erstellt: {timestamp_formatted}", styles["meta"]),
        Paragraph(f"E-Mails gesamt: {total_emails}", styles["meta"]),
        Spacer(1, 4 * mm),
    ]


def _build_zusammenfassung(
    styles: dict[str, ParagraphStyle],
    records: list[dict[str, Any]],
    dup_groups: list[dict[str, Any]],
) -> list[Any]:
    """Build the Zusammenfassung section flowables."""
    total_count = len(records)
    total_size = sum(r.get("total_size", 0) for r in records)
    dup_count = len(dup_groups)

    flowables: list[Any] = [
        Paragraph("Zusammenfassung", styles["heading1"]),
        Paragraph(f"Gesamt: {total_count} E-Mails", styles["body"]),
        Paragraph(f"Gesamt-Gr\xf6\xdfe: {total_size} Bytes", styles["body"]),
        Paragraph(f"Duplikatgruppen: {dup_count}", styles["body"]),
        Spacer(1, 4 * mm),
    ]
    return flowables


def _build_email_liste(
    styles: dict[str, ParagraphStyle],
    records: list[dict[str, Any]],
    usable_width: float,
) -> list[Any]:
    """Build the E-Mail-Liste section flowables (heading + table)."""
    # Column widths (must sum to usable_width)
    col_widths = [
        usable_width * 0.35,  # Betreff (subject)
        usable_width * 0.25,  # Von (sender)
        usable_width * 0.16,  # Datum (date)
        usable_width * 0.10,  # Anhänge (attachment count)
        usable_width * 0.14,  # Duplikate (dup flag)
    ]

    # Header row — German labels
    # NOTE: Anh\xe4nge = Anhänge, \xfc = ü  (Helvetica/WinAnsiEncoding)
    header = ["Betreff", "Von", "Datum", "Anh\xe4nge", "Duplikate"]

    sorted_records = sort_emails(records)

    # Build a ParagraphStyle for cell text so words wrap within columns
    cell_style = ParagraphStyle(
        "CellNormal",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        wordWrap="CJK",
        parent=getSampleStyleSheet()["Normal"],
    )
    cell_bold = ParagraphStyle(
        "CellBold",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        wordWrap="CJK",
        parent=getSampleStyleSheet()["Normal"],
    )

    header_row = [Paragraph(h, cell_bold) for h in header]
    data: list[list[Any]] = [header_row]
    for rec in sorted_records:
        subject = rec.get("subject", "") or ""
        sender = rec.get("sender", "") or ""
        date = rec.get("date", "") or ""

        # Attachment count: number of parts that are not body-only
        parts = rec.get("parts", [])
        attachment_count = len(parts)

        # Duplicate indicator
        dup_group_id = rec.get("dup_group_id")
        dup_marker = "Ja" if dup_group_id else "Nein"

        # Truncate subject/sender at generous limits — Paragraph wraps the rest
        subject_display = _esc(subject[:80] + "\u2026" if len(subject) > 80 else subject)
        sender_display = _esc(sender[:50] + "\u2026" if len(sender) > 50 else sender)
        # date is "YYYY-MM-DD HH:MM" — show only date portion
        date_display = _esc(date[:10] if len(date) >= 10 else date)

        data.append(
            [
                Paragraph(subject_display, cell_style),
                Paragraph(sender_display, cell_style),
                Paragraph(date_display, cell_style),
                Paragraph(str(attachment_count), cell_style),
                Paragraph(dup_marker, cell_style),
            ]
        )

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(_email_list_table_style())

    return [
        Paragraph("E-Mail-Liste", styles["heading1"]),
        Spacer(1, 2 * mm),
        table,
    ]


def _build_duplikate_gruppen(
    styles: dict[str, ParagraphStyle],
    records: list[dict[str, Any]],
    dup_groups: list[dict[str, Any]],
    usable_width: float,
) -> list[Any]:
    """Build the Duplikatgruppen section flowables (only when groups exist).

    Renders a section heading followed by one group-header paragraph + member table
    per duplicate group.  Groups are rendered in sort_dup_groups() order; members
    within each group table are rendered in sort_emails() order.

    Returns an empty list when dup_groups is empty (section is omitted entirely).
    """
    if not dup_groups:
        return []

    # Build a lookup: stable_id -> EmailRecord for member resolution.
    record_by_id: dict[str, dict[str, Any]] = {
        r["stable_id"]: r for r in records if r.get("stable_id")
    }

    # Column widths for the member table (3 columns: Betreff | Von | Datum)
    col_widths = [
        usable_width * 0.50,  # Betreff (subject)
        usable_width * 0.30,  # Von (sender)
        usable_width * 0.20,  # Datum (date)
    ]

    # ParagraphStyle for dup member table cells
    dup_cell = ParagraphStyle(
        "DupCell",
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        wordWrap="CJK",
        parent=getSampleStyleSheet()["Normal"],
    )
    dup_cell_bold = ParagraphStyle(
        "DupCellBold",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        wordWrap="CJK",
        parent=getSampleStyleSheet()["Normal"],
    )
    member_header_row = [Paragraph(h, dup_cell_bold) for h in ["Betreff", "Von", "Datum"]]

    flowables: list[Any] = [
        Paragraph("Duplikatgruppen", styles["heading1"]),
        Spacer(1, 2 * mm),
    ]

    sorted_groups = sort_dup_groups(dup_groups)

    for group_index, group in enumerate(sorted_groups, start=1):
        gid: str = group.get("group_id", "")
        member_count: int = group.get("member_count", 0)
        # Group header label: 'Gruppe N: <group_id[:8]>  N Mitglieder'
        # Note: no parens in label — parens require PDF string escaping which breaks
        # the simple Tj regex extraction used in tests.
        group_label = f"Gruppe {group_index}: {gid[:8]}  {member_count} Mitglieder"
        flowables.append(Paragraph(group_label, styles["body"]))
        flowables.append(Spacer(1, 1 * mm))

        # Resolve member records and sort by canonical email ordering.
        member_ids: list[str] = group.get("member_email_ids", [])
        member_records = [
            record_by_id[mid] for mid in member_ids if mid in record_by_id
        ]
        sorted_members = sort_emails(member_records)

        data: list[list[Any]] = [member_header_row]
        for member in sorted_members:
            subject = member.get("subject", "") or ""
            sender = member.get("sender", "") or ""
            date = member.get("date", "") or ""

            subject_display = _esc(subject[:80] + "\u2026" if len(subject) > 80 else subject)
            sender_display = _esc(sender[:50] + "\u2026" if len(sender) > 50 else sender)
            date_display = _esc(date[:10] if len(date) >= 10 else date)

            data.append([
                Paragraph(subject_display, dup_cell),
                Paragraph(sender_display, dup_cell),
                Paragraph(date_display, dup_cell),
            ])

        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(_dup_member_table_style())
        flowables.append(table)
        flowables.append(Spacer(1, 3 * mm))

    return flowables

# ── Public API ────────────────────────────────────────────────────────────────


def build_report_pdf(
    records: list[dict[str, Any]],
    dup_groups: list[dict[str, Any]],
    timestamp_str: str,
) -> bytes:
    """Render a deterministic German PDF report and return raw bytes.

    Parameters
    ----------
    records:
        List of EmailRecord dicts (from models.EmailRecord).  May be empty.
    dup_groups:
        List of DupGroupRecord dicts (from models.DupGroupRecord).  May be empty.
    timestamp_str:
        ISO 8601 datetime string, e.g. ``"2024-06-15T10:00:00+00:00"``.
        Parsed via ``runtime.parse_report_timestamp()``; raises ValueError
        on unparseable or date-only input.

    Returns
    -------
    bytes
        Valid PDF bytes.  Same inputs always produce the same bytes (assuming
        configure_deterministic_pdf() has been called, which this function does
        unconditionally before building the document).
    """
    # ── 1. Determinism guard — must be called FIRST, before any RL object ────
    configure_deterministic_pdf()

    # ── 2. Parse and format the report timestamp ──────────────────────────────
    dt = parse_report_timestamp(timestamp_str)
    timestamp_formatted = format_report_timestamp(dt)

    # ── 3. Build the story (Platypus flowable list) ───────────────────────────
    styles = _make_styles()
    page_width, page_height = A4
    usable_width = page_width - _LEFT_MARGIN - _RIGHT_MARGIN

    story: list[Any] = []

    # Section 1: Deckblatt / Meta
    story.extend(_build_deckblatt(styles, timestamp_formatted, len(records)))

    # Section 2: Zusammenfassung
    story.extend(_build_zusammenfassung(styles, records, dup_groups))

    # Section 3: E-Mail-Liste
    story.extend(_build_email_liste(styles, records, usable_width))

    # Section 4: Duplikatgruppen (only rendered when groups exist)
    story.extend(_build_duplikate_gruppen(styles, records, dup_groups, usable_width))

    # ── 4. Render to bytes ────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=_LEFT_MARGIN,
        rightMargin=_RIGHT_MARGIN,
        topMargin=_TOP_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
        title="Maildir-Bericht",
        author="maildir_report",
        subject=f"Maildir-Bericht {timestamp_formatted}",
        creator="maildir_report/0.1.0",
    )
    doc.build(story)
    return buf.getvalue()

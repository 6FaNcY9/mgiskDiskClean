# tests/test_body_cc_extraction.py
"""Tests for CC and body_text extraction in parser.parse_email_file.

These tests are RED before Task 4 (parser implementation).
Run: pytest tests/test_body_cc_extraction.py -v
"""
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from maildir_report.parser import parse_email_file


def _write_eml(tmp_path, msg, name="test.eml"):
    p = tmp_path / name
    p.write_bytes(msg.as_bytes())
    return str(p)


def _base_headers(msg, mid="<1@test>"):
    msg["From"] = "sender@x.com"
    msg["To"] = "recipient@x.com"
    msg["Subject"] = "test subject"
    msg["Message-ID"] = mid
    msg["Date"] = "Mon, 1 Jan 2024 10:00:00 +0000"


# ── cc_addrs ──────────────────────────────────────────────────────────────────

def test_cc_header_extracted(tmp_path):
    msg = MIMEText("body text", "plain", "utf-8")
    _base_headers(msg, "<cc1@test>")
    msg["Cc"] = "cc1@x.com, cc2@x.com"
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert rec["cc_addrs"] == "cc1@x.com, cc2@x.com"


def test_cc_header_empty_when_missing(tmp_path):
    msg = MIMEText("body text", "plain", "utf-8")
    _base_headers(msg, "<cc2@test>")
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert rec["cc_addrs"] == ""


# ── body_text ─────────────────────────────────────────────────────────────────

def test_body_text_plain_extracted(tmp_path):
    msg = MIMEText("Hello world body text", "plain", "utf-8")
    _base_headers(msg, "<bt1@test>")
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert "Hello world body text" in rec["body_text"]


def test_body_text_empty_when_no_text_part(tmp_path):
    msg = MIMEMultipart()
    _base_headers(msg, "<bt2@test>")
    att = MIMEApplication(b"pdfbytes", Name="doc.pdf")
    att["Content-Disposition"] = 'attachment; filename="doc.pdf"'
    msg.attach(att)
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert rec["body_text"] == ""


def test_body_text_multipart_alternative(tmp_path):
    """Multipart/alternative: plain text part is preferred over HTML."""
    msg = MIMEMultipart("alternative")
    _base_headers(msg, "<bt3@test>")
    msg.attach(MIMEText("plain version of content", "plain", "utf-8"))
    msg.attach(MIMEText("<b>html version</b>", "html", "utf-8"))
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert "plain version of content" in rec["body_text"]


def test_body_text_windows1251_charset(tmp_path):
    """Cyrillic windows-1251 body decodes without raising."""
    cyrillic = "Привіт архів"
    raw_bytes = cyrillic.encode("windows-1251")
    raw_eml = (
        b"From: a@x.com\r\nTo: b@x.com\r\nSubject: charset\r\n"
        b"Message-ID: <bt4@test>\r\nDate: Mon, 1 Jan 2024 10:00:00 +0000\r\n"
        b"Content-Type: text/plain; charset=windows-1251\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\n"
    ) + raw_bytes
    p = tmp_path / "cyrillic.eml"
    p.write_bytes(raw_eml)
    rec = parse_email_file(str(p), "INBOX")
    assert isinstance(rec["body_text"], str)
    assert len(rec["body_text"]) > 0


def test_body_text_is_string_type(tmp_path):
    msg = MIMEText("some body", "plain", "utf-8")
    _base_headers(msg, "<bt5@test>")
    rec = parse_email_file(_write_eml(tmp_path, msg), "INBOX")
    assert isinstance(rec["body_text"], str)
    assert isinstance(rec["cc_addrs"], str)

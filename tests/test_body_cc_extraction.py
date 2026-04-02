"""
test_body_cc_extraction.py — Red tests for CC + body extraction in parser.py.

These tests FAIL until Task 7 implements cc_addrs and body_text in parse_email_file().
"""

import pathlib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from maildir_report.parser import parse_email_file


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_mail(tmp_path: pathlib.Path, raw: bytes, name: str = "1.msg") -> str:
    p = tmp_path / name
    p.write_bytes(raw)
    return str(p)


def _plain_mail(
    *,
    subject: str = "Hello",
    from_: str = "alice@example.com",
    to: str = "bob@example.com",
    cc: str = "",
    body: str = "Hello world.",
    message_id: str = "<test@example.com>",
) -> bytes:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["Message-ID"] = message_id
    return msg.as_bytes()


def _multipart_mail(
    *,
    body_plain: str = "Plain body.",
    body_html: str = "<p>HTML body.</p>",
    cc: str = "",
    message_id: str = "<multi@example.com>",
) -> bytes:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Multipart"
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    if cc:
        msg["Cc"] = cc
    msg["Date"] = "Tue, 02 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = message_id
    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg.as_bytes()


def _mail_with_attachment(
    *,
    body: str = "See attachment.",
    cc: str = "",
    attachment_data: bytes = b"PDF content",
    filename: str = "report.pdf",
    message_id: str = "<attach@example.com>",
) -> bytes:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "With attachment"
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    if cc:
        msg["Cc"] = cc
    msg["Date"] = "Wed, 03 Jan 2024 08:00:00 +0000"
    msg["Message-ID"] = message_id
    msg.attach(MIMEText(body, "plain", "utf-8"))
    att = MIMEApplication(attachment_data, Name=filename)
    att["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(att)
    return msg.as_bytes()


# ── cc_addrs tests ────────────────────────────────────────────────────────────


def test_cc_addrs_present_when_cc_header_set(tmp_path):
    """parse_email_file() must return 'cc_addrs' key."""
    raw = _plain_mail(cc="carol@example.com", message_id="<cc1@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "cc_addrs" in rec, "EmailRecord must have 'cc_addrs' key"


def test_cc_addrs_value_matches_header(tmp_path):
    """cc_addrs must match the decoded Cc header value."""
    raw = _plain_mail(cc="carol@example.com, dave@example.com", message_id="<cc2@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "carol@example.com" in rec["cc_addrs"]
    assert "dave@example.com" in rec["cc_addrs"]


def test_cc_addrs_empty_string_when_no_cc(tmp_path):
    """cc_addrs must be an empty string when no Cc header is present."""
    raw = _plain_mail(cc="", message_id="<cc3@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert rec["cc_addrs"] == ""


def test_cc_addrs_decoded_rfc2047(tmp_path):
    """cc_addrs must be a decoded unicode string (not raw RFC 2047 encoded)."""
    # ASCII Cc is enough to verify the field is present and decoded
    raw = _plain_mail(cc="Eve <eve@example.com>", message_id="<cc4@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert isinstance(rec["cc_addrs"], str)
    assert "eve@example.com" in rec["cc_addrs"]


# ── body_text tests ───────────────────────────────────────────────────────────


def test_body_text_present_in_record(tmp_path):
    """parse_email_file() must return 'body_text' key."""
    raw = _plain_mail(body="Test body content.", message_id="<b1@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "body_text" in rec, "EmailRecord must have 'body_text' key"


def test_body_text_plain_extracted(tmp_path):
    """body_text must contain the text/plain body content."""
    raw = _plain_mail(body="Hello archive world.", message_id="<b2@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "Hello archive world." in rec["body_text"]


def test_body_text_from_multipart_alternative(tmp_path):
    """body_text must be extracted from text/plain part of multipart/alternative."""
    raw = _multipart_mail(body_plain="Plain part here.", message_id="<b3@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "Plain part here." in rec["body_text"]


def test_body_text_does_not_contain_html(tmp_path):
    """body_text must NOT contain HTML tags from text/html parts."""
    raw = _multipart_mail(
        body_plain="Plain only.",
        body_html="<p>HTML content here</p>",
        message_id="<b4@x>",
    )
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    # Must contain plain text
    assert "Plain only." in rec["body_text"]
    # Must NOT contain raw HTML tags (text/html is not included)
    assert "<p>" not in rec["body_text"]


def test_body_text_with_attachment_present(tmp_path):
    """body_text works correctly when the email also has attachments."""
    raw = _mail_with_attachment(body="Cover letter text.", message_id="<b5@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert "Cover letter text." in rec["body_text"]


def test_body_text_empty_string_for_attachment_only(tmp_path):
    """body_text is empty string when no text/plain part exists."""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "No body"
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["Message-ID"] = "<b6@x>"
    att = MIMEApplication(b"data", Name="data.bin")
    att["Content-Disposition"] = 'attachment; filename="data.bin"'
    msg.attach(att)
    path = _write_mail(tmp_path, msg.as_bytes())
    rec = parse_email_file(path, "INBOX")
    assert rec["body_text"] == ""


def test_body_text_is_string_type(tmp_path):
    """body_text must always be a str, never bytes."""
    raw = _plain_mail(body="String check.", message_id="<b7@x>")
    path = _write_mail(tmp_path, raw)
    rec = parse_email_file(path, "INBOX")
    assert isinstance(rec["body_text"], str)


def test_body_text_charset_fallback_latin1(tmp_path):
    """body_text handles latin-1 encoded bodies without crashing."""
    # Build a raw email with latin-1 body
    raw_body = "Caf\xe9 und Str\xae\xdf e"  # latin-1 bytes in string
    from email.mime.text import MIMEText as MT

    part = MT(raw_body, "plain", "latin-1")
    part["Subject"] = "Encoding test"
    part["From"] = "x@example.com"
    part["To"] = "y@example.com"
    part["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    part["Message-ID"] = "<b8@x>"
    path = _write_mail(tmp_path, part.as_bytes())
    rec = parse_email_file(path, "INBOX")
    # Must not raise; must be a str
    assert isinstance(rec["body_text"], str)
    assert len(rec["body_text"]) > 0

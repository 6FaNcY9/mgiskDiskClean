"""
test_rfc822.py — Task 7: nested message/rfc822 handling.

Contract being tested
---------------------
- A message containing a message/rfc822 part is NOT silently dropped.
- The outer email record carries has_nested_messages=True.
- The nested message appears in parts with category="nested_message",
  mime="message/rfc822", filename="[nested message]".
- Parsing the same fixture twice produces byte-for-byte identical records
  (determinism / stable hashing).
- Emails WITHOUT a nested message carry has_nested_messages=False.
"""

from __future__ import annotations

import pathlib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import email as _email
from email.message import Message

import pytest

from maildir_report.parser import parse_email_file, MailParseError


# ── fixture builders ──────────────────────────────────────────────────────────


def _make_inner_message(
    subject: str = "Original message",
    sender: str = "original@example.com",
    body: str = "This is the original body.",
    message_id: str = "<original@example.com>",
) -> bytes:
    """Build a minimal RFC 2822 message to be used as the nested payload."""
    inner = MIMEText(body, "plain", "utf-8")
    inner["Subject"] = subject
    inner["From"] = sender
    inner["To"] = "recipient@example.com"
    inner["Date"] = "Mon, 01 Jan 2024 09:00:00 +0000"
    inner["Message-ID"] = message_id
    return inner.as_bytes()


def _make_forward_with_nested(
    inner_bytes: bytes,
    outer_subject: str = "Fwd: Original message",
    outer_message_id: str = "<fwd@example.com>",
) -> bytes:
    """Build an outer message that forwards inner_bytes as a message/rfc822 part."""
    outer = MIMEMultipart()
    outer["Subject"] = outer_subject
    outer["From"] = "forwarder@example.com"
    outer["To"] = "newrecip@example.com"
    outer["Date"] = "Tue, 02 Jan 2024 10:00:00 +0000"
    outer["Message-ID"] = outer_message_id

    outer.attach(MIMEText("See forwarded message below.", "plain"))

    # Attach the nested message as message/rfc822
    nested_part = _email.message_from_bytes(inner_bytes)
    container = Message()
    container["Content-Type"] = "message/rfc822"
    container["Content-Disposition"] = "inline"
    container.set_payload([nested_part])  # type: ignore[arg-type]
    outer.attach(container)

    return outer.as_bytes()


def _write_mail(directory: pathlib.Path, filename: str, raw: bytes) -> pathlib.Path:
    path = directory / filename
    path.write_bytes(raw)
    return path


def _make_maildir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Maildir skeleton under tmp_path."""
    root = tmp_path / "Maildir"
    (root / "cur").mkdir(parents=True)
    (root / "new").mkdir(parents=True)
    (root / "tmp").mkdir(parents=True)
    return root


# ── tests ─────────────────────────────────────────────────────────────────────


class TestRfc822NestedPresence:
    """nested message/rfc822 part appears in parts inventory."""

    def test_nested_part_present_in_parts(self, tmp_path: pathlib.Path) -> None:
        """A forwarded message must produce a nested_message part, not be dropped."""
        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record = parse_email_file(str(path), folder="INBOX")

        nested_parts = [p for p in record["parts"] if p["category"] == "nested_message"]
        assert len(nested_parts) == 1, (
            f"Expected 1 nested_message part, got {len(nested_parts)}. "
            f"All parts: {[(p['category'], p['mime']) for p in record['parts']]}"
        )

    def test_nested_part_mime_type(self, tmp_path: pathlib.Path) -> None:
        """Nested part must carry mime='message/rfc822'."""
        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record = parse_email_file(str(path), folder="INBOX")

        nested_parts = [p for p in record["parts"] if p["category"] == "nested_message"]
        assert nested_parts[0]["mime"] == "message/rfc822"

    def test_nested_part_filename(self, tmp_path: pathlib.Path) -> None:
        """Nested part must carry the synthetic filename '[nested message]'."""
        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record = parse_email_file(str(path), folder="INBOX")

        nested_parts = [p for p in record["parts"] if p["category"] == "nested_message"]
        assert nested_parts[0]["filename"] == "[nested message]"

    def test_nested_part_has_stable_id(self, tmp_path: pathlib.Path) -> None:
        """Nested part must carry a non-empty stable_id (SHA-256 hex)."""
        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record = parse_email_file(str(path), folder="INBOX")

        nested_parts = [p for p in record["parts"] if p["category"] == "nested_message"]
        sid = nested_parts[0]["stable_id"]
        assert isinstance(sid, str) and len(sid) == 64, (
            f"stable_id should be a 64-char SHA-256 hex, got: {sid!r}"
        )

    def test_nested_part_has_content_hash(self, tmp_path: pathlib.Path) -> None:
        """Nested part must carry a non-empty content_hash (SHA-256 hex)."""
        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record = parse_email_file(str(path), folder="INBOX")

        nested_parts = [p for p in record["parts"] if p["category"] == "nested_message"]
        chash = nested_parts[0]["content_hash"]
        assert isinstance(chash, str) and len(chash) == 64


class TestRfc822Flag:
    """has_nested_messages flag on email record."""

    def test_flag_true_when_nested_present(self, tmp_path: pathlib.Path) -> None:
        """Record must have has_nested_messages=True for a forwarded message."""
        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record = parse_email_file(str(path), folder="INBOX")

        assert record["has_nested_messages"] is True

    def test_flag_false_when_no_nested(self, tmp_path: pathlib.Path) -> None:
        """Ordinary email must have has_nested_messages=False."""
        msg = MIMEText("Hello", "plain", "utf-8")
        msg["Subject"] = "Plain email"
        msg["From"] = "a@example.com"
        msg["To"] = "b@example.com"
        msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
        msg["Message-ID"] = "<plain@example.com>"

        path = _write_mail(tmp_path, "plain_msg", msg.as_bytes())
        record = parse_email_file(str(path), folder="INBOX")

        assert record["has_nested_messages"] is False

    def test_flag_false_with_attachment_but_no_nested(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Email with a PDF attachment but no nested message must be False."""
        outer = MIMEMultipart()
        outer["Subject"] = "PDF email"
        outer["From"] = "a@example.com"
        outer["To"] = "b@example.com"
        outer["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
        outer["Message-ID"] = "<pdf@example.com>"
        outer.attach(MIMEText("See attached.", "plain"))
        att = MIMEApplication(b"%PDF-1.4 fake", _subtype="pdf")
        att.add_header("Content-Disposition", "attachment", filename="doc.pdf")
        outer.attach(att)

        path = _write_mail(tmp_path, "pdf_msg", outer.as_bytes())
        record = parse_email_file(str(path), folder="INBOX")

        assert record["has_nested_messages"] is False


class TestRfc822Determinism:
    """Parsing the same nested-message fixture twice is byte-for-byte identical."""

    def test_repeated_parse_identical_record(self, tmp_path: pathlib.Path) -> None:
        """Two parses of the same file produce identical parts and stable IDs."""
        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record_a = parse_email_file(str(path), folder="INBOX")
        record_b = parse_email_file(str(path), folder="INBOX")

        assert record_a["stable_id"] == record_b["stable_id"]
        assert record_a["has_nested_messages"] == record_b["has_nested_messages"]

        parts_a = record_a["parts"]
        parts_b = record_b["parts"]
        assert len(parts_a) == len(parts_b)
        for pa, pb in zip(parts_a, parts_b):
            assert pa["stable_id"] == pb["stable_id"]
            assert pa["content_hash"] == pb["content_hash"]
            assert pa["category"] == pb["category"]
            assert pa["filename"] == pb["filename"]

    def test_stable_id_different_for_different_nested(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two distinct nested messages must produce different stable_ids on their nested part."""
        inner_a = _make_inner_message(subject="Message A", message_id="<a@example.com>")
        inner_b = _make_inner_message(subject="Message B", message_id="<b@example.com>")

        raw_a = _make_forward_with_nested(
            inner_a, outer_message_id="<fwd-a@example.com>"
        )
        raw_b = _make_forward_with_nested(
            inner_b, outer_message_id="<fwd-b@example.com>"
        )

        path_a = _write_mail(tmp_path, "fwd_a", raw_a)
        path_b = _write_mail(tmp_path, "fwd_b", raw_b)

        record_a = parse_email_file(str(path_a), folder="INBOX")
        record_b = parse_email_file(str(path_b), folder="INBOX")

        nested_a = next(
            p for p in record_a["parts"] if p["category"] == "nested_message"
        )
        nested_b = next(
            p for p in record_b["parts"] if p["category"] == "nested_message"
        )

        assert nested_a["stable_id"] != nested_b["stable_id"]
        assert nested_a["content_hash"] != nested_b["content_hash"]

    def test_content_hash_non_empty(self, tmp_path: pathlib.Path) -> None:
        """Nested message content_hash must not be the empty-bytes SHA-256."""
        EMPTY_SHA256 = (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

        inner = _make_inner_message()
        raw = _make_forward_with_nested(inner)
        path = _write_mail(tmp_path, "fwd_msg", raw)

        record = parse_email_file(str(path), folder="INBOX")
        nested = next(p for p in record["parts"] if p["category"] == "nested_message")

        assert nested["content_hash"] != EMPTY_SHA256, (
            "Nested message had empty payload — sub-message serialisation likely failed"
        )

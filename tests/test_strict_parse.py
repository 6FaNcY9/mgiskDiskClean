"""
test_strict_parse.py — TDD tests for Task 3: strict Maildir parser.

Contract being tested
---------------------
- parse_email_file(filepath) returns an EmailRecord (never None).
- Any unreadable file raises MailParseError with the filepath in the message.
- Any structurally unparseable file raises MailParseError with the filepath.
- MIME parts are NOT dropped by arbitrary size thresholds.
- scan_maildir(root) returns exactly one EmailRecord per scanned file.
- Unreadable files in scan cause MailParseError (no silent skip).
- scan_maildir results are deterministically ordered.
"""

from __future__ import annotations

import email
import os
import pathlib
import stat
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Any

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_mail(directory: pathlib.Path, filename: str, raw: bytes) -> pathlib.Path:
    """Write raw bytes to directory/filename and return the full path."""
    path = directory / filename
    path.write_bytes(raw)
    return path


def _simple_mail(
    subject: str = "Hello",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    date: str = "Mon, 01 Jan 2024 10:00:00 +0000",
    body: str = "This is the body.",
    message_id: str = "<test@example.com>",
) -> bytes:
    """Build a minimal valid RFC 2822 message as bytes."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = date
    msg["Message-ID"] = message_id
    return msg.as_bytes()


def _mail_with_attachment(
    attachment_bytes: bytes,
    filename: str = "doc.pdf",
    mime_type: str = "application/pdf",
    message_id: str = "<attach@example.com>",
) -> bytes:
    """Build a multipart message with one attachment."""
    msg = MIMEMultipart()
    msg["Subject"] = "With attachment"
    msg["From"] = "sender@example.com"
    msg["To"] = "recv@example.com"
    msg["Date"] = "Tue, 15 Feb 2024 08:00:00 +0000"
    msg["Message-ID"] = message_id
    msg.attach(MIMEText("See attached.", "plain"))
    maintype, subtype = mime_type.split("/", 1)
    attachment = MIMEApplication(attachment_bytes, _subtype=subtype)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)
    return msg.as_bytes()


def _make_maildir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Maildir skeleton under tmp_path and return the root."""
    root = tmp_path / "Maildir"
    (root / "cur").mkdir(parents=True)
    (root / "new").mkdir(parents=True)
    (root / "tmp").mkdir(parents=True)
    return root


# ── import guard ──────────────────────────────────────────────────────────────


class TestStrictParseImports:
    """Parser module and exception types must be importable from the package."""

    def test_strict_parse_module_importable(self):
        """maildir_report.parser must be importable."""
        from maildir_report import parser  # noqa: F401

    def test_strict_parse_exception_importable(self):
        """MailParseError must be importable from maildir_report.parser."""
        from maildir_report.parser import MailParseError  # noqa: F401

    def test_strict_parse_function_importable(self):
        """parse_email_file() must be importable from maildir_report.parser."""
        from maildir_report.parser import parse_email_file  # noqa: F401

    def test_strict_parse_scan_importable(self):
        """scan_maildir() must be importable from maildir_report.parser."""
        from maildir_report.parser import scan_maildir  # noqa: F401


# ── MailParseError ────────────────────────────────────────────────────────────


class TestStrictParseMailParseError:
    """MailParseError must carry context for debugging."""

    def test_strict_parse_error_is_exception(self):
        from maildir_report.parser import MailParseError

        assert issubclass(MailParseError, Exception)

    def test_strict_parse_error_carries_filepath(self):
        """MailParseError must store the filepath that caused the failure."""
        from maildir_report.parser import MailParseError

        err = MailParseError(filepath="/some/path/file.msg", reason="test reason")
        assert "/some/path/file.msg" in str(err)

    def test_strict_parse_error_carries_reason(self):
        from maildir_report.parser import MailParseError

        err = MailParseError(filepath="/some/path/file.msg", reason="cannot read")
        assert "cannot read" in str(err)


# ── parse_email_file: success cases ──────────────────────────────────────────


class TestStrictParseSuccess:
    """parse_email_file() must return a complete EmailRecord on valid input."""

    def test_strict_parse_returns_email_record(self, tmp_path):
        """parse_email_file returns an EmailRecord dict for a valid message."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(cur, "simple.msg", _simple_mail())
        result = parse_email_file(str(path), folder="INBOX")

        assert isinstance(result, dict)
        assert result["filepath"] == str(path)

    def test_strict_parse_result_has_required_fields(self, tmp_path):
        """EmailRecord must contain all required fields from models.py."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(
            cur,
            "req_fields.msg",
            _simple_mail(
                subject="Test Subject",
                sender="alice@example.com",
                to="bob@example.com",
                message_id="<req@example.com>",
            ),
        )
        rec = parse_email_file(str(path), folder="INBOX")

        assert "filepath" in rec
        assert "message_id" in rec
        assert "subject" in rec
        assert "date" in rec
        assert "date_day" in rec
        assert "sender" in rec
        assert "to" in rec
        assert "folder" in rec
        assert "total_size" in rec
        assert "parts" in rec
        assert "stable_id" in rec

    def test_strict_parse_stable_id_not_none(self, tmp_path):
        """stable_id must be a non-empty string (never None or index-based)."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(cur, "sid.msg", _simple_mail())
        rec = parse_email_file(str(path), folder="INBOX")

        assert isinstance(rec["stable_id"], str)
        assert len(rec["stable_id"]) > 0

    def test_strict_parse_stable_id_is_deterministic(self, tmp_path):
        """Parsing the same file twice must yield the same stable_id."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(cur, "det.msg", _simple_mail())
        r1 = parse_email_file(str(path), folder="INBOX")
        r2 = parse_email_file(str(path), folder="INBOX")
        assert r1["stable_id"] == r2["stable_id"]

    def test_strict_parse_total_size_matches_file(self, tmp_path):
        """total_size must match actual byte-length of the raw file."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        raw = _simple_mail(body="Exact size body.")
        path = _write_mail(cur, "size.msg", raw)
        rec = parse_email_file(str(path), folder="INBOX")
        assert rec["total_size"] == len(raw)

    def test_strict_parse_folder_preserved(self, tmp_path):
        """folder field must equal the folder argument passed to parse_email_file."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(cur, "folder.msg", _simple_mail())
        rec = parse_email_file(str(path), folder=".Sent")
        assert rec["folder"] == ".Sent"

    def test_strict_parse_message_id_extracted(self, tmp_path):
        """message_id field must equal the Message-ID header value."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(
            cur, "msgid.msg", _simple_mail(message_id="<unique-id-123@example.com>")
        )
        rec = parse_email_file(str(path), folder="INBOX")
        assert "<unique-id-123@example.com>" in rec["message_id"]

    def test_strict_parse_subject_decoded(self, tmp_path):
        """subject field must contain decoded subject text."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(cur, "subj.msg", _simple_mail(subject="Hallo Welt"))
        rec = parse_email_file(str(path), folder="INBOX")
        assert rec["subject"] == "Hallo Welt"


# ── parse_email_file: small-part retention ────────────────────────────────────


class TestStrictParseSmallParts:
    """Parts must NOT be silently dropped by arbitrary size thresholds."""

    def test_strict_parse_small_attachment_not_dropped(self, tmp_path):
        """An attachment smaller than 128 bytes must NOT be silently discarded."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        # 20 bytes — well below the legacy 128-byte threshold
        tiny_payload = b"X" * 20
        raw = _mail_with_attachment(
            tiny_payload,
            filename="tiny.pdf",
            mime_type="application/pdf",
            message_id="<tiny@example.com>",
        )
        path = _write_mail(cur, "tiny.msg", raw)
        rec = parse_email_file(str(path), folder="INBOX")

        part_filenames = [p["filename"] for p in rec["parts"]]
        assert "tiny.pdf" in part_filenames, (
            f"tiny.pdf (20 bytes) was silently dropped. parts={part_filenames}"
        )

    def test_strict_parse_zero_byte_attachment_included(self, tmp_path):
        """A zero-byte attachment must appear in parts (not silently dropped)."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        raw = _mail_with_attachment(
            b"",  # zero bytes
            filename="empty.dat",
            mime_type="application/octet-stream",
            message_id="<zero@example.com>",
        )
        path = _write_mail(cur, "zero.msg", raw)
        rec = parse_email_file(str(path), folder="INBOX")

        part_filenames = [p["filename"] for p in rec["parts"]]
        assert "empty.dat" in part_filenames, (
            f"empty.dat (0 bytes) was silently dropped. parts={part_filenames}"
        )

    def test_strict_parse_127_byte_attachment_not_dropped(self, tmp_path):
        """127-byte attachment (boundary of legacy threshold) must not be dropped."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        raw = _mail_with_attachment(
            b"B" * 127,
            filename="boundary.txt",
            mime_type="text/plain",
            message_id="<bound@example.com>",
        )
        path = _write_mail(cur, "boundary.msg", raw)
        rec = parse_email_file(str(path), folder="INBOX")

        part_filenames = [p["filename"] for p in rec["parts"]]
        assert "boundary.txt" in part_filenames, (
            f"boundary.txt (127 bytes) was silently dropped. parts={part_filenames}"
        )


# ── parse_email_file: failure cases ──────────────────────────────────────────


class TestStrictParseFailures:
    """parse_email_file() must raise MailParseError (never return None)."""

    def test_strict_parse_unreadable_file_raises(self, tmp_path):
        """Unreadable file (no read permission) raises MailParseError with filepath."""
        from maildir_report.parser import MailParseError, parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(cur, "unreadable.msg", _simple_mail())
        # Remove read permission
        path.chmod(0o000)
        try:
            with pytest.raises(MailParseError) as exc_info:
                parse_email_file(str(path), folder="INBOX")
            assert str(path) in str(exc_info.value), (
                f"MailParseError message must include filepath. Got: {exc_info.value}"
            )
        finally:
            # Restore so tmp cleanup works
            path.chmod(0o600)

    def test_strict_parse_nonexistent_file_raises(self, tmp_path):
        """Nonexistent file raises MailParseError with filepath in message."""
        from maildir_report.parser import MailParseError, parse_email_file

        missing = str(tmp_path / "ghost.msg")
        with pytest.raises(MailParseError) as exc_info:
            parse_email_file(missing, folder="INBOX")
        assert missing in str(exc_info.value)

    def test_strict_parse_truncated_file_raises(self, tmp_path):
        """Completely empty / truncated file raises MailParseError with filepath."""
        from maildir_report.parser import MailParseError, parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        # An empty file is not a valid RFC 2822 message
        path = _write_mail(cur, "empty.msg", b"")
        with pytest.raises(MailParseError) as exc_info:
            parse_email_file(str(path), folder="INBOX")
        assert str(path) in str(exc_info.value)

    def test_strict_parse_never_returns_none(self, tmp_path):
        """parse_email_file must NEVER silently return None — must raise instead."""
        from maildir_report.parser import MailParseError, parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(cur, "missing.msg", b"")
        result = None
        with pytest.raises(MailParseError):
            result = parse_email_file(str(path), folder="INBOX")
        # If we reach here it raised correctly — result must still be None
        assert result is None, "Should have raised before assigning result"


# ── scan_maildir: success ─────────────────────────────────────────────────────


class TestStrictParseScanSuccess:
    """scan_maildir() must return exactly N records for N valid mail files."""

    def test_strict_parse_scan_returns_all_records(self, tmp_path):
        """Fixture with N mail files => scan returns exactly N EmailRecords."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        n = 5
        for i in range(n):
            _write_mail(
                cur,
                f"mail_{i:03d}.msg",
                _simple_mail(
                    message_id=f"<mail{i}@example.com>",
                    subject=f"Subject {i}",
                ),
            )
        records = scan_maildir(str(root))
        assert len(records) == n, f"Expected {n} records, got {len(records)}"

    def test_strict_parse_scan_includes_new_folder(self, tmp_path):
        """Files in new/ are included in the scan."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        _write_mail(
            root / "new",
            "in_new.msg",
            _simple_mail(message_id="<new@example.com>"),
        )
        _write_mail(
            root / "cur",
            "in_cur.msg",
            _simple_mail(message_id="<cur@example.com>"),
        )
        records = scan_maildir(str(root))
        assert len(records) == 2

    def test_strict_parse_scan_excludes_tmp_folder(self, tmp_path):
        """Files in tmp/ must be excluded from the scan."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        _write_mail(
            root / "tmp",
            "in_tmp.msg",
            _simple_mail(message_id="<tmp@example.com>"),
        )
        _write_mail(
            root / "cur",
            "real.msg",
            _simple_mail(message_id="<real@example.com>"),
        )
        records = scan_maildir(str(root))
        assert len(records) == 1

    def test_strict_parse_scan_skips_dotfiles(self, tmp_path):
        """Files starting with '.' must be excluded (Maildir convention)."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", ".hidden", _simple_mail())
        _write_mail(
            root / "cur",
            "visible.msg",
            _simple_mail(message_id="<vis@example.com>"),
        )
        records = scan_maildir(str(root))
        assert len(records) == 1

    def test_strict_parse_scan_result_is_deterministic(self, tmp_path):
        """Two scans of the same Maildir must return identically-ordered results."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        for i in range(4):
            _write_mail(
                cur,
                f"m{i}.msg",
                _simple_mail(
                    message_id=f"<det{i}@example.com>",
                    date=f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                ),
            )
        ids_run1 = [r["stable_id"] for r in scan_maildir(str(root))]
        ids_run2 = [r["stable_id"] for r in scan_maildir(str(root))]
        assert ids_run1 == ids_run2, "scan_maildir ordering must be deterministic"

    def test_strict_parse_scan_47_files(self, tmp_path):
        """Fixture with 47 mail files => scan returns exactly 47 records."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        n = 47
        for i in range(n):
            _write_mail(
                cur,
                f"mail_{i:04d}.msg",
                _simple_mail(
                    message_id=f"<m{i}@example.com>",
                    subject=f"Mail number {i}",
                ),
            )
        records = scan_maildir(str(root))
        assert len(records) == n


# ── scan_maildir: failure propagation ────────────────────────────────────────


class TestStrictParseScanFailures:
    """scan_maildir() must NOT silently skip unreadable files — must raise."""

    def test_strict_parse_scan_unreadable_file_raises(self, tmp_path):
        """One unreadable file among valid files causes MailParseError (strict mode)."""
        from maildir_report.parser import MailParseError, scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        _write_mail(cur, "ok1.msg", _simple_mail(message_id="<ok1@example.com>"))
        _write_mail(cur, "ok2.msg", _simple_mail(message_id="<ok2@example.com>"))
        bad = _write_mail(cur, "bad.msg", _simple_mail(message_id="<bad@example.com>"))
        bad.chmod(0o000)
        try:
            with pytest.raises(MailParseError) as exc_info:
                scan_maildir(str(root))
            assert str(bad) in str(exc_info.value), (
                f"MailParseError must name the bad file. Got: {exc_info.value}"
            )
        finally:
            bad.chmod(0o600)

    def test_strict_parse_scan_empty_file_raises(self, tmp_path):
        """One empty (unparseable) file causes MailParseError, not silent skip."""
        from maildir_report.parser import MailParseError, scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        _write_mail(cur, "good.msg", _simple_mail(message_id="<good@example.com>"))
        bad = _write_mail(cur, "empty.msg", b"")  # empty = unparseable
        with pytest.raises(MailParseError) as exc_info:
            scan_maildir(str(root))
        assert str(bad) in str(exc_info.value)

    def test_strict_parse_scan_no_silent_none(self, tmp_path):
        """scan_maildir must never silently drop None results — it must raise."""
        from maildir_report.parser import MailParseError, scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        # All valid
        _write_mail(cur, "a.msg", _simple_mail(message_id="<a@example.com>"))
        _write_mail(cur, "b.msg", _simple_mail(message_id="<b@example.com>"))
        # Introduce broken file
        bad = _write_mail(cur, "broken.msg", b"")
        with pytest.raises(MailParseError):
            scan_maildir(str(root))
        bad.unlink()
        # After removing bad file, scan must succeed with exactly 2 records
        records = scan_maildir(str(root))
        assert len(records) == 2


# ── integration: parts & IDs round-trip ──────────────────────────────────────


class TestStrictParseIntegration:
    """End-to-end round-trip: parse -> record -> IDs consistent with models."""

    def test_strict_parse_part_has_stable_id(self, tmp_path):
        """Each PartRecord in the result must carry a non-empty stable_id."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        raw = _mail_with_attachment(
            b"A" * 500,
            filename="report.pdf",
            message_id="<pid@example.com>",
        )
        path = _write_mail(cur, "withpart.msg", raw)
        rec = parse_email_file(str(path), folder="INBOX")
        for part in rec["parts"]:
            assert "stable_id" in part
            assert isinstance(part["stable_id"], str)
            assert len(part["stable_id"]) > 0

    def test_strict_parse_part_has_content_hash(self, tmp_path):
        """Each PartRecord must carry a content_hash (SHA-256 hex string)."""
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        import hashlib

        payload = b"deterministic payload"
        raw = _mail_with_attachment(
            payload,
            filename="check.pdf",
            message_id="<hash@example.com>",
        )
        path = _write_mail(cur, "hash.msg", raw)
        rec = parse_email_file(str(path), folder="INBOX")
        pdf_parts = [p for p in rec["parts"] if p.get("filename") == "check.pdf"]
        assert len(pdf_parts) >= 1
        part = pdf_parts[0]
        assert part["content_hash"] == hashlib.sha256(payload).hexdigest()

    def test_strict_parse_stable_id_uses_ids_module(self, tmp_path):
        """EmailRecord.stable_id must be consistent with ids.email_stable_id()."""
        from maildir_report.ids import email_stable_id
        from maildir_report.parser import parse_email_file

        cur = tmp_path / "cur"
        cur.mkdir()
        path = _write_mail(
            cur, "ids_compat.msg", _simple_mail(message_id="<compat@example.com>")
        )
        rec = parse_email_file(str(path), folder="INBOX")
        expected_id = email_stable_id(rec)
        assert rec["stable_id"] == expected_id

    def test_strict_parse_scan_all_stable_ids_unique(self, tmp_path):
        """All EmailRecords from a scan must have unique stable_ids."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        for i in range(10):
            _write_mail(
                cur,
                f"unique_{i}.msg",
                _simple_mail(message_id=f"<unique{i}@example.com>"),
            )
        records = scan_maildir(str(root))
        ids = [r["stable_id"] for r in records]
        assert len(ids) == len(set(ids)), "All stable_ids must be unique"

"""
test_task2b_attachments_index.py — Tests for Task 2b: attachment extraction
and mailbox indexing.

Covers:
  1. Attachment extraction writes files with <sha256>_<size>.<ext> naming.
  2. Extraction is idempotent: two runs produce no duplicate files, stable count.
  3. Zero-byte and synthetic inline parts are not written.
  4. Path safety: MIME filename directory traversal is neutralised.
  5. Collision-safe writes: existing file with same hash is skipped.
  6. extract_attachments CLI --help exits 0.
  7. Index DB schema: both tables and required columns exist.
  8. Index DB contains required minimum fields per the spec.
  9. Indexing is idempotent: running twice gives same row counts.
  10. Global index path is populated when provided.
  11. Email-attachment linkage: email_stable_id FK matches emails.stable_id.
  12. Filename search scenario: original_filename queryable.
  13. index_mailbox CLI --help exits 0.
  14. index_mailbox raises FileNotFoundError when maildir_root is absent.
  15. _safe_extension returns "bin" for empty/missing filename.
  16. _stored_filename produces correct <sha256>_<size>.<ext> format.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from maildir_report.extract_attachments import (
    ExtractResult,
    _safe_extension,
    _stored_filename,
    extract_attachments,
    main as extract_main,
)
from maildir_report.index_mailbox import (
    IndexResult,
    index_mailbox,
    main as index_main,
)


# ── minimal RFC-2822 fixture builders ─────────────────────────────────────────


def _mail_with_attachment(
    attachment_bytes: bytes,
    filename: str = "report.pdf",
    mime_type: str = "application/pdf",
    message_id: str = "<attach@example.com>",
    subject: str = "With attachment",
    sender: str = "sender@example.com",
) -> bytes:
    """Return a minimal multipart email with one named attachment."""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "recv@example.com"
    msg["Date"] = "Tue, 15 Feb 2024 08:00:00 +0000"
    msg["Message-ID"] = message_id
    msg.attach(MIMEText("See attached.", "plain"))
    maintype, subtype = mime_type.split("/", 1)
    att = MIMEApplication(attachment_bytes, _subtype=subtype)
    att.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(att)
    return msg.as_bytes()


def _simple_mail(
    subject: str = "Hello",
    message_id: str = "<simple@example.com>",
) -> bytes:
    """Return a minimal plain-text email (no attachments)."""
    msg = MIMEText("Just text.", "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["Message-ID"] = message_id
    return msg.as_bytes()


def _make_maildir(base: pathlib.Path) -> pathlib.Path:
    """Create a Maildir skeleton under *base* and return the Maildir root."""
    root = base / ".maildir"
    (root / "cur").mkdir(parents=True, exist_ok=True)
    (root / "new").mkdir(parents=True, exist_ok=True)
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    return root


def _write_cur(maildir: pathlib.Path, filename: str, raw: bytes) -> pathlib.Path:
    """Write *raw* to maildir/cur/<filename> and return the path."""
    path = maildir / "cur" / filename
    path.write_bytes(raw)
    return path


def _make_data_root(
    tmp_path: pathlib.Path,
    mailbox: str = "test_mailbox",
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create the data_root layout expected by index_mailbox.

    Returns (data_root, maildir_root).
    """
    data_root = tmp_path / "data" / "mailboxes" / mailbox
    maildir_root = data_root / "maildir" / ".maildir"
    maildir_root.mkdir(parents=True, exist_ok=True)
    (maildir_root / "cur").mkdir(parents=True, exist_ok=True)
    (maildir_root / "new").mkdir(parents=True, exist_ok=True)
    (maildir_root / "tmp").mkdir(parents=True, exist_ok=True)
    (data_root / "attachments").mkdir(parents=True, exist_ok=True)
    return data_root, maildir_root


# ── 1. Extraction writes files with correct naming ────────────────────────────


class TestExtractionNaming:
    """Extracted files use <sha256>_<size>.<ext> naming."""

    def test_pdf_attachment_written_with_sha256_size_ext(self, tmp_path):
        """A PDF attachment should land as <sha256>_<size>.pdf."""
        import hashlib

        payload = b"fake pdf bytes"
        sha = hashlib.sha256(payload).hexdigest()
        size = len(payload)

        maildir = _make_maildir(tmp_path)
        raw = _mail_with_attachment(payload, filename="report.pdf")
        _write_cur(maildir, "email1", raw)

        out_dir = tmp_path / "attachments"
        result = extract_attachments(str(maildir), str(out_dir))

        expected_name = f"{sha}_{size}.pdf"
        assert (out_dir / expected_name).exists(), (
            f"Expected file {expected_name} in {out_dir}"
        )
        assert len(result.written) == 1
        assert result.total_attachments == 1

    def test_extension_matches_original_filename(self, tmp_path):
        """Extension in stored filename matches the original attachment extension."""
        payload = b"fake docx bytes"
        maildir = _make_maildir(tmp_path)
        raw = _mail_with_attachment(
            payload,
            filename="letter.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        _write_cur(maildir, "email2", raw)

        out_dir = tmp_path / "attachments"
        result = extract_attachments(str(maildir), str(out_dir))

        assert result.total_attachments == 1
        stored = result.written[0]
        assert stored.endswith(".docx"), f"Expected .docx extension, got {stored!r}"

    def test_multiple_attachments_from_one_email(self, tmp_path):
        """All named attachments from a multipart email are written."""
        msg = MIMEMultipart()
        msg["Subject"] = "Two attachments"
        msg["From"] = "a@b.com"
        msg["To"] = "c@d.com"
        msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
        msg["Message-ID"] = "<two@example.com>"
        msg.attach(MIMEText("body", "plain"))

        for i, payload in enumerate([b"attachment one", b"attachment two"], 1):
            att = MIMEApplication(payload, _subtype="octet-stream")
            att.add_header("Content-Disposition", "attachment", filename=f"file{i}.bin")
            msg.attach(att)

        maildir = _make_maildir(tmp_path)
        _write_cur(maildir, "email_two_atts", msg.as_bytes())

        out_dir = tmp_path / "attachments"
        result = extract_attachments(str(maildir), str(out_dir))

        assert result.total_attachments == 2
        assert len(result.written) == 2


# ── 2. Idempotence ─────────────────────────────────────────────────────────────


class TestExtractionIdempotence:
    """Running extraction twice yields no new files and stable counts."""

    def test_second_run_writes_no_new_files(self, tmp_path):
        """After two extraction runs, total on-disk file count equals first run."""
        payload = b"idempotent attachment"
        maildir = _make_maildir(tmp_path)
        _write_cur(
            maildir,
            "email_idem",
            _mail_with_attachment(payload, filename="idem.pdf"),
        )

        out_dir = tmp_path / "attachments"
        result1 = extract_attachments(str(maildir), str(out_dir))
        result2 = extract_attachments(str(maildir), str(out_dir))

        files_on_disk = list(out_dir.iterdir())
        assert len(files_on_disk) == 1, (
            f"Expected exactly 1 file on disk after 2 runs, found {files_on_disk}"
        )
        assert len(result1.written) == 1
        assert len(result2.written) == 0
        assert len(result2.skipped_duplicate) == 1

    def test_second_run_total_attachments_stable(self, tmp_path):
        """total_attachments count is identical across runs."""
        payload = b"stable count test"
        maildir = _make_maildir(tmp_path)
        _write_cur(
            maildir,
            "email_stable",
            _mail_with_attachment(payload, filename="stable.pdf"),
        )

        out_dir = tmp_path / "attachments"
        r1 = extract_attachments(str(maildir), str(out_dir))
        r2 = extract_attachments(str(maildir), str(out_dir))

        assert r1.total_attachments == r2.total_attachments

    def test_same_content_two_emails_one_file(self, tmp_path):
        """Two emails sharing the same attachment payload produce one file."""
        payload = b"shared payload bytes"
        maildir = _make_maildir(tmp_path)
        _write_cur(
            maildir,
            "email_shared1",
            _mail_with_attachment(
                payload, filename="shared.pdf", message_id="<sha1@ex.com>"
            ),
        )
        _write_cur(
            maildir,
            "email_shared2",
            _mail_with_attachment(
                payload, filename="shared.pdf", message_id="<sha2@ex.com>"
            ),
        )

        out_dir = tmp_path / "attachments"
        result = extract_attachments(str(maildir), str(out_dir))

        files_on_disk = list(out_dir.iterdir())
        assert len(files_on_disk) == 1, (
            f"Expected 1 file for identical payloads, found {files_on_disk}"
        )
        assert result.total_attachments == 2
        assert len(result.written) == 1
        assert len(result.skipped_duplicate) == 1


# ── 3. Zero-byte and inline parts not written ─────────────────────────────────


class TestSkippedParts:
    """Certain parts are excluded from extraction."""

    def test_plain_text_email_no_files_written(self, tmp_path):
        """A plain-text email with no attachments writes no files."""
        maildir = _make_maildir(tmp_path)
        _write_cur(maildir, "plain", _simple_mail())

        out_dir = tmp_path / "attachments"
        result = extract_attachments(str(maildir), str(out_dir))

        assert result.total_attachments == 0
        assert result.written == []

    def test_zero_byte_attachment_not_written(self, tmp_path):
        """A zero-byte attachment part is not written to disk."""
        maildir = _make_maildir(tmp_path)
        raw = _mail_with_attachment(b"", filename="empty.pdf")
        _write_cur(maildir, "zero_att", raw)

        out_dir = tmp_path / "attachments"
        result = extract_attachments(str(maildir), str(out_dir))

        # Zero-byte parts are excluded.
        assert result.total_attachments == 0
        assert result.written == []


# ── 4. Path safety ────────────────────────────────────────────────────────────


class TestPathSafety:
    """MIME filename traversal cannot escape the output directory."""

    def test_traversal_filename_stays_in_output_dir(self, tmp_path):
        """../../evil.pdf in MIME filename stays inside output_root."""
        payload = b"traversal test payload"
        maildir = _make_maildir(tmp_path)
        raw = _mail_with_attachment(payload, filename="../../evil.pdf")
        _write_cur(maildir, "traversal_email", raw)

        out_dir = tmp_path / "attachments"
        result = extract_attachments(str(maildir), str(out_dir))

        if result.written:
            for p in result.written:
                assert str(out_dir) in p, (
                    f"Written file {p!r} is outside output_dir {out_dir}"
                )
                # Must not write to any parent path.
                assert pathlib.Path(p).parent == out_dir, (
                    f"Expected file directly in {out_dir}, found {p!r}"
                )


# ── 5. Extension helpers ──────────────────────────────────────────────────────


class TestSafeExtension:
    """_safe_extension returns a safe, sensible extension."""

    def test_empty_filename_returns_bin(self):
        assert _safe_extension("") == "bin"

    def test_none_equivalent_empty_returns_bin(self):
        # None is not a valid str, but empty string is the sentinel.
        assert _safe_extension("") == "bin"

    def test_pdf_extension_returned(self):
        assert _safe_extension("report.pdf") == "pdf"

    def test_extension_lowercase_preserved(self):
        assert _safe_extension("IMAGE.JPG") == "JPG"

    def test_traversal_in_filename_only_ext_used(self):
        ext = _safe_extension("../../etc/passwd")
        # No dot suffix in 'passwd', so falls back to 'bin'.
        assert ext == "bin"

    def test_path_separator_stripped_from_ext(self):
        # Pathological: extension with slash.
        ext = _safe_extension("file.pdf/extra")
        # pathlib gives suffix of 'file.pdf/extra' as '' (treated as no ext).
        assert "/" not in ext

    def test_no_extension_returns_bin(self):
        assert _safe_extension("README") == "bin"

    def test_dotfile_returns_bin(self):
        # .gitignore has no extension after the leading dot by pathlib convention.
        assert _safe_extension(".gitignore") == "bin"


class TestStoredFilename:
    """_stored_filename produces <sha256>_<size>.<ext> format."""

    def test_format_is_sha256_underscore_size_dot_ext(self):
        sha = "a" * 64
        result = _stored_filename(sha, 1234, "report.pdf")
        assert result == f"{'a' * 64}_1234.pdf"

    def test_zero_size(self):
        sha = "b" * 64
        result = _stored_filename(sha, 0, "empty.txt")
        assert result == f"{'b' * 64}_0.txt"

    def test_no_filename_uses_bin(self):
        sha = "c" * 64
        result = _stored_filename(sha, 10, "")
        assert result == f"{'c' * 64}_10.bin"


# ── 6. extract_attachments CLI ────────────────────────────────────────────────


class TestExtractCLI:
    """CLI interface for extract_attachments."""

    def test_help_exits_zero(self):
        """python -m maildir_report.extract_attachments --help must exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "maildir_report.extract_attachments", "--help"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(pathlib.Path(__file__).parent.parent / "src"),
            },
        )
        assert result.returncode == 0, (
            f"--help exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )

    def test_help_shows_prog_name(self):
        """--help output should mention the program name."""
        result = subprocess.run(
            [sys.executable, "-m", "maildir_report.extract_attachments", "--help"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(pathlib.Path(__file__).parent.parent / "src"),
            },
        )
        assert "extract" in result.stdout.lower()

    def test_main_missing_args_exits_nonzero(self):
        """main() with missing required args must exit non-zero."""
        with pytest.raises(SystemExit) as exc_info:
            extract_main([])
        assert exc_info.value.code != 0

    def test_main_runs_on_empty_maildir(self, tmp_path):
        """main() with a valid (empty) maildir exits 0."""
        maildir = _make_maildir(tmp_path)
        out_dir = tmp_path / "attachments"

        exit_code = extract_main(
            ["--maildir-root", str(maildir), "--output-root", str(out_dir)]
        )
        assert exit_code == 0


# ── 7. Index DB schema ────────────────────────────────────────────────────────


class TestIndexSchema:
    """SQLite index contains the required tables and columns."""

    def test_emails_table_exists(self, tmp_path):
        """emails table must exist in the per-mailbox index."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        index_mailbox("testbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "emails" in tables

    def test_attachments_table_exists(self, tmp_path):
        """attachments table must exist in the per-mailbox index."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        index_mailbox("testbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "attachments" in tables

    def test_emails_has_required_columns(self, tmp_path):
        """emails table must have all required minimum columns."""
        required = {
            "mailbox",
            "stable_id",
            "filepath",
            "folder",
            "date",
            "from_addr",
            "subject",
            "total_size_bytes",
        }
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        index_mailbox("testbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        info = conn.execute("PRAGMA table_info(emails)").fetchall()
        conn.close()

        cols = {row[1] for row in info}
        missing = required - cols
        assert not missing, f"emails table missing columns: {missing}"

    def test_attachments_has_required_columns(self, tmp_path):
        """attachments table must have all required minimum columns."""
        required = {
            "sha256",
            "size",
            "mime",
            "original_filename",
            "stored_path",
            "email_stable_id",
        }
        data_root, maildir_root = _make_data_root(tmp_path)
        payload = b"schema check att"
        _write_cur(
            maildir_root,
            "msg_att",
            _mail_with_attachment(payload, filename="check.pdf"),
        )

        index_mailbox("testbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        info = conn.execute("PRAGMA table_info(attachments)").fetchall()
        conn.close()

        cols = {row[1] for row in info}
        missing = required - cols
        assert not missing, f"attachments table missing columns: {missing}"


# ── 8. Index DB minimum fields populated ─────────────────────────────────────


class TestIndexContent:
    """Index DB rows contain the required minimum fields."""

    def test_email_row_fields_populated(self, tmp_path):
        """Each email row has non-empty mailbox, stable_id, filepath, folder."""
        data_root, maildir_root = _make_data_root(tmp_path, mailbox="my_mailbox")
        _write_cur(maildir_root, "msg1", _simple_mail(subject="Test Subject"))

        index_mailbox("my_mailbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM emails").fetchall()
        conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert row["mailbox"] == "my_mailbox"
        assert row["stable_id"] != ""
        assert row["filepath"] != ""
        assert row["subject"] == "Test Subject"
        assert row["total_size_bytes"] > 0

    def test_attachment_row_fields_populated(self, tmp_path):
        """Each attachment row has non-empty sha256, size, mime, stored_path, email_stable_id."""
        data_root, maildir_root = _make_data_root(tmp_path)
        payload = b"fields check payload"
        _write_cur(
            maildir_root,
            "msg_att",
            _mail_with_attachment(payload, filename="doc.pdf"),
        )

        index_mailbox("testbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM attachments").fetchall()
        conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert len(row["sha256"]) == 64
        assert row["size"] == len(payload)
        assert row["mime"] != ""
        assert "doc.pdf" in row["original_filename"]
        assert row["stored_path"] != ""
        assert row["email_stable_id"] != ""


# ── 9. Indexing idempotence ───────────────────────────────────────────────────


class TestIndexIdempotence:
    """Running index_mailbox twice gives identical row counts."""

    def test_email_row_count_stable_after_two_runs(self, tmp_path):
        """emails table row count is unchanged after re-indexing."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail(message_id="<idem1@ex.com>"))
        _write_cur(maildir_root, "msg2", _simple_mail(message_id="<idem2@ex.com>"))

        index_mailbox("testbox", str(data_root))
        r1_count = _count_rows(data_root / "index.sqlite", "emails")

        index_mailbox("testbox", str(data_root))
        r2_count = _count_rows(data_root / "index.sqlite", "emails")

        assert r1_count == 2
        assert r2_count == 2, (
            f"Email row count changed after second run: {r1_count} → {r2_count}"
        )

    def test_attachment_row_count_stable_after_two_runs(self, tmp_path):
        """attachments table row count is unchanged after re-indexing."""
        data_root, maildir_root = _make_data_root(tmp_path)
        payload = b"idempotent index attachment"
        _write_cur(
            maildir_root,
            "msg_att",
            _mail_with_attachment(
                payload, filename="idem.pdf", message_id="<idem3@ex.com>"
            ),
        )

        index_mailbox("testbox", str(data_root))
        r1_count = _count_rows(data_root / "index.sqlite", "attachments")

        index_mailbox("testbox", str(data_root))
        r2_count = _count_rows(data_root / "index.sqlite", "attachments")

        assert r1_count == 1
        assert r2_count == 1, (
            f"Attachment row count changed after second run: {r1_count} → {r2_count}"
        )


def _count_rows(db_path: pathlib.Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    conn.close()
    return count


# ── 10. Global index ──────────────────────────────────────────────────────────


class TestGlobalIndex:
    """Global index path is populated when provided."""

    def test_global_index_created_at_specified_path(self, tmp_path):
        """When --global-index is given, the file is created."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        global_idx = tmp_path / "global" / "mail_index.sqlite"
        result = index_mailbox("testbox", str(data_root), str(global_idx))

        assert global_idx.exists(), f"Global index not created at {global_idx}"
        assert result.global_index_path is not None

    def test_global_index_contains_emails(self, tmp_path):
        """Global index emails table has same row count as per-mailbox index."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail(message_id="<global1@ex.com>"))
        _write_cur(maildir_root, "msg2", _simple_mail(message_id="<global2@ex.com>"))

        global_idx = tmp_path / "global" / "mail_index.sqlite"
        index_mailbox("testbox", str(data_root), str(global_idx))

        per_count = _count_rows(data_root / "index.sqlite", "emails")
        global_count = _count_rows(global_idx, "emails")
        assert per_count == global_count == 2

    def test_global_index_idempotent(self, tmp_path):
        """Running twice with global index produces stable row count in global DB."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        global_idx = tmp_path / "global" / "mail_index.sqlite"
        index_mailbox("testbox", str(data_root), str(global_idx))
        count1 = _count_rows(global_idx, "emails")

        index_mailbox("testbox", str(data_root), str(global_idx))
        count2 = _count_rows(global_idx, "emails")

        assert count1 == count2 == 1


# ── 11. Email-attachment linkage ──────────────────────────────────────────────


class TestEmailAttachmentLinkage:
    """email_stable_id in attachments table matches emails.stable_id."""

    def test_attachment_links_to_email(self, tmp_path):
        """email_stable_id in attachments must match an emails.stable_id row."""
        data_root, maildir_root = _make_data_root(tmp_path)
        payload = b"linkage test payload"
        _write_cur(
            maildir_root,
            "msg_link",
            _mail_with_attachment(payload, filename="link.pdf"),
        )

        index_mailbox("testbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        email_ids = {
            r[0] for r in conn.execute("SELECT stable_id FROM emails").fetchall()
        }
        att_email_ids = {
            r[0]
            for r in conn.execute("SELECT email_stable_id FROM attachments").fetchall()
        }
        conn.close()

        assert att_email_ids <= email_ids, (
            f"Attachment email_stable_id values {att_email_ids} "
            f"not all present in emails.stable_id {email_ids}"
        )


# ── 12. Filename search scenario ─────────────────────────────────────────────


class TestFilenameSearch:
    """original_filename in attachments table is queryable."""

    def test_search_by_original_filename(self, tmp_path):
        """Querying original_filename by LIKE finds the correct attachment."""
        data_root, maildir_root = _make_data_root(tmp_path)
        payload = b"search scenario payload"
        _write_cur(
            maildir_root,
            "msg_search",
            _mail_with_attachment(
                payload,
                filename="quarterly_report.pdf",
                message_id="<search@ex.com>",
            ),
        )

        index_mailbox("testbox", str(data_root))

        conn = sqlite3.connect(str(data_root / "index.sqlite"))
        rows = conn.execute(
            "SELECT original_filename, email_stable_id "
            "FROM attachments WHERE original_filename LIKE ?",
            ("%quarterly%",),
        ).fetchall()
        conn.close()

        assert len(rows) == 1, f"Expected 1 row for LIKE %quarterly%, got {rows!r}"
        assert "quarterly_report.pdf" in rows[0][0]


# ── 13. index_mailbox CLI ────────────────────────────────────────────────────


class TestIndexCLI:
    """CLI interface for index_mailbox."""

    def test_help_exits_zero(self):
        """python -m maildir_report.index_mailbox --help must exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "maildir_report.index_mailbox", "--help"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(pathlib.Path(__file__).parent.parent / "src"),
            },
        )
        assert result.returncode == 0, (
            f"--help exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )

    def test_help_shows_prog_name(self):
        """--help output mentions the program name."""
        result = subprocess.run(
            [sys.executable, "-m", "maildir_report.index_mailbox", "--help"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(pathlib.Path(__file__).parent.parent / "src"),
            },
        )
        assert "index" in result.stdout.lower()

    def test_main_missing_args_exits_nonzero(self):
        """main() with missing required args must exit non-zero."""
        with pytest.raises(SystemExit) as exc_info:
            index_main([])
        assert exc_info.value.code != 0

    def test_main_missing_maildir_returns_one(self, tmp_path):
        """main() with a nonexistent maildir returns 1."""
        exit_code = index_main(
            [
                "--mailbox",
                "test",
                "--data-root",
                str(tmp_path / "nonexistent"),
            ]
        )
        assert exit_code == 1

    def test_main_with_valid_maildir_exits_zero(self, tmp_path):
        """main() with a valid maildir exits 0."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        exit_code = index_main(["--mailbox", "testbox", "--data-root", str(data_root)])
        assert exit_code == 0


# ── 14. index_mailbox raises on missing maildir ───────────────────────────────


class TestIndexMailboxErrors:
    """index_mailbox raises FileNotFoundError for nonexistent maildir."""

    def test_raises_file_not_found_when_maildir_missing(self, tmp_path):
        """FileNotFoundError is raised when maildir/.maildir/ doesn't exist."""
        data_root = tmp_path / "empty_root"
        data_root.mkdir()

        with pytest.raises(FileNotFoundError) as exc_info:
            index_mailbox("testbox", str(data_root))

        assert "maildir" in str(exc_info.value).lower()


# ── 15. IndexResult fields ────────────────────────────────────────────────────


class TestIndexResult:
    """IndexResult dataclass fields are correctly populated."""

    def test_result_emails_indexed_count(self, tmp_path):
        """emails_indexed matches the number of email files in the maildir."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail(message_id="<r1@ex.com>"))
        _write_cur(maildir_root, "msg2", _simple_mail(message_id="<r2@ex.com>"))

        result = index_mailbox("testbox", str(data_root))

        assert result.emails_indexed == 2

    def test_result_attachments_indexed_count(self, tmp_path):
        """attachments_indexed matches the number of extractable parts."""
        data_root, maildir_root = _make_data_root(tmp_path)
        payload = b"result count payload"
        _write_cur(
            maildir_root,
            "msg_att",
            _mail_with_attachment(payload, filename="count.pdf"),
        )

        result = index_mailbox("testbox", str(data_root))

        assert result.attachments_indexed == 1

    def test_result_index_path_is_absolute(self, tmp_path):
        """index_path in result is an absolute path ending in index.sqlite."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        result = index_mailbox("testbox", str(data_root))

        assert pathlib.Path(result.index_path).is_absolute()
        assert result.index_path.endswith("index.sqlite")

    def test_result_global_index_none_when_not_requested(self, tmp_path):
        """global_index_path is None when not requested."""
        data_root, maildir_root = _make_data_root(tmp_path)
        _write_cur(maildir_root, "msg1", _simple_mail())

        result = index_mailbox("testbox", str(data_root))

        assert result.global_index_path is None

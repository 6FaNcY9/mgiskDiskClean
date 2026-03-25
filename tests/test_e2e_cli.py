"""
test_e2e_cli.py — End-to-end tests for Task 14: CLI entrypoint.

Contracts being tested
----------------------
cli.py
    build_pipeline(maildir_path, output_dir, timestamp_str) -> None
        - Generates all three output artifacts in output_dir.
        - report.pdf is non-empty valid PDF bytes (starts with %PDF-).
        - manifest.json is valid JSON with all required keys.
        - decisions.csv has a header row + one data row per email.
        - manifest["pdf_sha256"] matches sha256_hex(report.pdf bytes).
        - Inventory reconciliation: disk files == parsed records.
        - Raises MailParseError on unreadable/unparseable files (strict mode).
        - Raises ValueError on bad --timestamp input.

    main(argv) -> int
        - Returns 0 on a successful run.
        - Returns 1 when any error occurs (parse failure, bad timestamp, etc.).
        - --help exits with 0 (standard argparse behaviour).
        - Missing --timestamp causes non-zero exit.

All test functions have "e2e" in their name so ``pytest -k e2e`` selects them.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import pathlib
import re
import zlib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import pytest


# ── PDF text extraction helper (matches test_pdf_german_headers.py) ────────────


def _decode_pdf_octal(raw: bytes) -> str:
    """Decode PDF-encoded text operand bytes to a Python string."""
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
    """Extract all text drawn via Tj operators from a ReportLab PDF."""
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


# ── synthetic Maildir fixture helpers ─────────────────────────────────────────


def _simple_mail(
    subject: str = "Test-Betreff",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    date: str = "Mon, 01 Jan 2024 10:00:00 +0000",
    body: str = "Einfacher Nachrichtentext.",
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
    subject: str = "Mit Anhang",
    message_id: str = "<attach@example.com>",
    payload: bytes = b"PDF content here",
    filename: str = "doc.pdf",
    date: str = "Tue, 02 Jan 2024 12:00:00 +0000",
) -> bytes:
    """Build an RFC 2822 message with a binary attachment."""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = "sender@example.com"
    msg["To"] = "recv@example.com"
    msg["Date"] = date
    msg["Message-ID"] = message_id
    body = MIMEText("Siehe Anhang.", "plain", "utf-8")
    msg.attach(body)
    att = MIMEApplication(payload, Name=filename)
    att["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(att)
    return msg.as_bytes()


def _make_maildir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Maildir skeleton and return the root path."""
    root = tmp_path / "Maildir"
    (root / "cur").mkdir(parents=True)
    (root / "new").mkdir(parents=True)
    (root / "tmp").mkdir(parents=True)
    return root


FIXED_TS = "2024-06-15T10:00:00+00:00"


# ── e2e: three output files created ──────────────────────────────────────────


class TestE2eOutputsCreated:
    """build_pipeline writes all three artifacts to output_dir."""

    def test_e2e_pdf_file_created(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<m1@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        assert (out / "report.pdf").exists()

    def test_e2e_manifest_json_created(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<m2@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        assert (out / "manifest.json").exists()

    def test_e2e_decisions_csv_created(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<m3@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        assert (out / "decisions.csv").exists()

    def test_e2e_output_dir_created_if_missing(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<m4@e2e.test>"))
        out = tmp_path / "nested" / "deep" / "out"
        assert not out.exists()
        build_pipeline(str(root), str(out), FIXED_TS)
        assert out.exists()


# ── e2e: PDF content validity ─────────────────────────────────────────────────


class TestE2ePdfContent:
    """PDF output has correct format and German content."""

    def test_e2e_pdf_starts_with_pdf_magic(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<pdf1@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        pdf_bytes = (out / "report.pdf").read_bytes()
        assert pdf_bytes.startswith(b"%PDF-")

    def test_e2e_pdf_is_non_empty(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<pdf2@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        assert (out / "report.pdf").stat().st_size > 0

    def test_e2e_pdf_contains_german_headers(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<pdf3@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        pdf_bytes = (out / "report.pdf").read_bytes()
        text = _extract_pdf_text(pdf_bytes)
        assert "E-Mail-Liste" in text


# ── e2e: manifest JSON consistency ────────────────────────────────────────────


class TestE2eManifestConsistency:
    """Manifest JSON is valid, complete, and consistent with the PDF."""

    def test_e2e_manifest_is_valid_json(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<mj1@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        text = (out / "manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(text)
        assert isinstance(manifest, dict)

    def test_e2e_manifest_has_required_keys(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<mj2@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        for key in (
            "schema_version",
            "generated_at",
            "inventory",
            "email_stable_ids",
            "dup_groups",
            "pdf_sha256",
        ):
            assert key in manifest, f"missing key: {key}"

    def test_e2e_manifest_pdf_sha256_matches_actual_pdf(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Core invariant: manifest.pdf_sha256 == sha256(report.pdf)."""
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<mj3@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        pdf_bytes = (out / "report.pdf").read_bytes()
        actual_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["pdf_sha256"] == actual_sha256

    def test_e2e_manifest_email_count_matches_maildir(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"mail{i}").write_bytes(
                _simple_mail(message_id=f"<mc{i}@e2e.test>")
            )
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["inventory"]["email_count"] == 3

    def test_e2e_manifest_generated_at_matches_timestamp(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<mts1@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["generated_at"] == FIXED_TS

    def test_e2e_manifest_passes_invariants(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline
        from maildir_report.manifest import validate_manifest_invariants

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(
            _simple_mail(message_id="<minv1@e2e.test>")
        )
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        # Should not raise:
        validate_manifest_invariants(manifest)


# ── e2e: decisions CSV consistency ────────────────────────────────────────────


class TestE2eDecisionsTemplate:
    """Decisions CSV contains correct columns and one row per email."""

    def test_e2e_decisions_has_header_row(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<dc1@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        content = (out / "decisions.csv").read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(content))
        assert reader.fieldnames == ["stable_id", "filepath", "decision"]

    def test_e2e_decisions_row_count_matches_email_count(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        for i in range(4):
            (root / "cur" / f"mail{i}").write_bytes(
                _simple_mail(message_id=f"<drc{i}@e2e.test>")
            )
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        content = (out / "decisions.csv").read_text(encoding="utf-8")
        rows = list(csv.DictReader(io.StringIO(content)))
        assert len(rows) == 4

    def test_e2e_decisions_stable_ids_match_manifest(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(
            _simple_mail(message_id="<dsid1@e2e.test>")
        )
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        content = (out / "decisions.csv").read_text(encoding="utf-8")
        csv_ids = [row["stable_id"] for row in csv.DictReader(io.StringIO(content))]
        assert set(csv_ids) == set(manifest["email_stable_ids"])

    def test_e2e_decisions_decision_field_is_empty(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<ddf1@e2e.test>"))
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        content = (out / "decisions.csv").read_text(encoding="utf-8")
        rows = list(csv.DictReader(io.StringIO(content)))
        for row in rows:
            assert row["decision"] == ""


# ── e2e: duplicate groups propagate correctly ─────────────────────────────────


class TestE2eDuplicateGroups:
    """Emails sharing attachment bytes are grouped in manifest and PDF."""

    def test_e2e_duplicate_emails_show_dup_group_in_manifest(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        shared_payload = b"shared attachment content ABCDEF"
        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(
            _mail_with_attachment(
                message_id="<dup1@e2e.test>",
                payload=shared_payload,
                date="Mon, 01 Jan 2024 10:00:00 +0000",
            )
        )
        (root / "cur" / "mail2").write_bytes(
            _mail_with_attachment(
                message_id="<dup2@e2e.test>",
                payload=shared_payload,
                date="Tue, 02 Jan 2024 10:00:00 +0000",
            )
        )
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["inventory"]["dup_group_count"] == 1
        assert manifest["inventory"]["dup_email_count"] == 2

    def test_e2e_no_duplicates_when_unique_content(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(
            _mail_with_attachment(
                message_id="<uniq1@e2e.test>",
                payload=b"unique content AAA",
                date="Mon, 01 Jan 2024 10:00:00 +0000",
            )
        )
        (root / "cur" / "mail2").write_bytes(
            _mail_with_attachment(
                message_id="<uniq2@e2e.test>",
                payload=b"unique content BBB",
                date="Tue, 02 Jan 2024 10:00:00 +0000",
            )
        )
        out = tmp_path / "out"
        build_pipeline(str(root), str(out), FIXED_TS)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["inventory"]["dup_group_count"] == 0
        assert manifest["inventory"]["dup_email_count"] == 0


# ── e2e: determinism ──────────────────────────────────────────────────────────


class TestE2eDeterminism:
    """Two runs with same inputs and same timestamp produce identical outputs."""

    def test_e2e_two_runs_produce_identical_pdf_sha256(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<det1@e2e.test>"))
        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        build_pipeline(str(root), str(out1), FIXED_TS)
        build_pipeline(str(root), str(out2), FIXED_TS)
        sha1 = hashlib.sha256((out1 / "report.pdf").read_bytes()).hexdigest()
        sha2 = hashlib.sha256((out2 / "report.pdf").read_bytes()).hexdigest()
        assert sha1 == sha2

    def test_e2e_two_runs_produce_identical_manifest(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<det2@e2e.test>"))
        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        build_pipeline(str(root), str(out1), FIXED_TS)
        build_pipeline(str(root), str(out2), FIXED_TS)
        m1 = json.loads((out1 / "manifest.json").read_text(encoding="utf-8"))
        m2 = json.loads((out2 / "manifest.json").read_text(encoding="utf-8"))
        assert m1 == m2


# ── e2e: strict mode / error handling ─────────────────────────────────────────


class TestE2eStrictMode:
    """Unreadable files and bad timestamps cause errors, not silent skips."""

    def test_e2e_unreadable_file_raises_mail_parse_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.parser import MailParseError
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        bad_file = root / "cur" / "badmail"
        bad_file.write_bytes(b"")  # empty file → MailParseError
        out = tmp_path / "out"
        with pytest.raises(MailParseError):
            build_pipeline(str(root), str(out), FIXED_TS)

    def test_e2e_bad_timestamp_raises_value_error(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<bts1@e2e.test>"))
        out = tmp_path / "out"
        with pytest.raises(ValueError):
            build_pipeline(str(root), str(out), "not-a-timestamp")

    def test_e2e_date_only_timestamp_raises_value_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import build_pipeline

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<bts2@e2e.test>"))
        out = tmp_path / "out"
        with pytest.raises(ValueError):
            build_pipeline(str(root), str(out), "2024-06-15")


# ── e2e: main() / CLI argument parsing ────────────────────────────────────────


class TestE2eCliMain:
    """main() handles argv correctly and returns the right exit codes."""

    def test_e2e_main_returns_zero_on_success(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import main

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<cli1@e2e.test>"))
        out = tmp_path / "out"
        rc = main([str(root), str(out), "--timestamp", FIXED_TS])
        assert rc == 0

    def test_e2e_main_returns_one_on_bad_timestamp(
        self, tmp_path: pathlib.Path
    ) -> None:
        from maildir_report.cli import main

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<cli2@e2e.test>"))
        out = tmp_path / "out"
        rc = main([str(root), str(out), "--timestamp", "bad-ts"])
        assert rc == 1

    def test_e2e_main_returns_one_on_parse_error(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import main

        root = _make_maildir(tmp_path)
        (root / "cur" / "badmail").write_bytes(b"")  # empty → MailParseError
        out = tmp_path / "out"
        rc = main([str(root), str(out), "--timestamp", FIXED_TS])
        assert rc == 1

    def test_e2e_main_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        from maildir_report.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_e2e_main_missing_timestamp_exits_nonzero(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from maildir_report.cli import main

        root = _make_maildir(tmp_path)
        out = tmp_path / "out"
        with pytest.raises(SystemExit) as exc_info:
            main([str(root), str(out)])  # --timestamp missing → argparse error
        assert exc_info.value.code != 0

    def test_e2e_main_creates_all_three_outputs(self, tmp_path: pathlib.Path) -> None:
        from maildir_report.cli import main

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<cli3@e2e.test>"))
        out = tmp_path / "out"
        rc = main([str(root), str(out), "--timestamp", FIXED_TS])
        assert rc == 0
        assert (out / "report.pdf").exists()
        assert (out / "manifest.json").exists()
        assert (out / "decisions.csv").exists()

    def test_e2e_main_manifest_pdf_sha256_linkage(self, tmp_path: pathlib.Path) -> None:
        """main() result: manifest.pdf_sha256 == sha256(report.pdf) — end-to-end."""
        from maildir_report.cli import main

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(_simple_mail(message_id="<cli4@e2e.test>"))
        out = tmp_path / "out"
        rc = main([str(root), str(out), "--timestamp", FIXED_TS])
        assert rc == 0
        pdf_bytes = (out / "report.pdf").read_bytes()
        expected_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["pdf_sha256"] == expected_sha256

    def test_e2e_python_module_invocable(self, tmp_path: pathlib.Path) -> None:
        """python -m maildir_report works without ImportError.

        The subprocess needs PYTHONPATH=src so the src/ layout is importable
        without pip install -e . (matching how pytest adds pythonpath = ["src"]).
        """
        import os
        import subprocess
        import sys

        root = _make_maildir(tmp_path)
        (root / "cur" / "mail1").write_bytes(
            _simple_mail(message_id="<pymod1@e2e.test>")
        )
        out = tmp_path / "out"
        src_dir = str(pathlib.Path(__file__).parent.parent / "src")
        env = dict(os.environ)
        env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "maildir_report",
                str(root),
                str(out),
                "--timestamp",
                FIXED_TS,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert (out / "report.pdf").exists()
        assert (out / "manifest.json").exists()
        assert (out / "decisions.csv").exists()

"""
test_manifest.py — TDD tests for Task 12: audited manifest JSON generation.

Contracts being tested
----------------------
manifest.py
    build_manifest(records, dup_groups, timestamp_str, pdf_bytes=None) -> dict
        - Returns a schema-complete dict with all required top-level keys.
        - "generated_at" uses format_report_timestamp() — ISO 8601 UTC string.
        - "inventory" contains email_count, dup_email_count, dup_group_count,
          total_size_bytes.
        - "email_stable_ids" is a list of stable IDs in sort_emails() order.
        - "dup_groups" is a list of group dicts in sort_dup_groups() order.
        - "pdf_sha256" is a hex SHA-256 string when pdf_bytes is provided,
          None when pdf_bytes is None.
        - The manifest is JSON-serialisable without conversion.
        - Deterministic: same inputs always produce equal manifest dicts.

    validate_manifest_invariants(manifest) -> None
        - Returns None silently when all invariants hold.
        - Raises ManifestInvariantError when any invariant is violated.
        - Invariants: email_count == len(email_stable_ids),
                      dup_email_count <= email_count,
                      dup_group_count == len(dup_groups),
                      sum(member_count) == dup_email_count,
                      unique email_stable_ids,
                      unique group_ids.
"""

from __future__ import annotations

import hashlib
import json
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pathlib
import pytest


# ── fixture helpers ────────────────────────────────────────────────────────────


def _simple_mail(
    subject: str = "Test",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    date: str = "Mon, 01 Jan 2024 10:00:00 +0000",
    body: str = "Body text.",
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
    subject: str = "Attach",
    message_id: str = "<attach@example.com>",
    payload: bytes = b"PDF content",
    filename: str = "doc.pdf",
    date: str = "Mon, 01 Jan 2024 12:00:00 +0000",
) -> bytes:
    """Build an RFC 2822 message with a binary attachment."""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = "sender@example.com"
    msg["To"] = "recv@example.com"
    msg["Date"] = date
    msg["Message-ID"] = message_id
    body = MIMEText("See attached.", "plain", "utf-8")
    msg.attach(body)
    att = MIMEApplication(payload, Name=filename)
    att["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(att)
    return msg.as_bytes()


def _make_maildir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Maildir skeleton and return the root."""
    root = tmp_path / "Maildir"
    (root / "cur").mkdir(parents=True)
    (root / "new").mkdir(parents=True)
    (root / "tmp").mkdir(parents=True)
    return root


def _scan_and_group(root: pathlib.Path):
    """Scan a Maildir, run dedup, return (annotated_records, dup_groups)."""
    from maildir_report.dedup import group_emails
    from maildir_report.parser import scan_maildir

    records = scan_maildir(str(root))
    return group_emails(records)


TS = "2024-06-15T10:00:00+00:00"
GENERATED_AT = "2024-06-15T10:00:00+00:00"


# ── import / API surface ───────────────────────────────────────────────────────


class TestManifestImports:
    """All public symbols must be importable from maildir_report.manifest."""

    def test_build_manifest_importable(self):
        from maildir_report.manifest import build_manifest  # noqa: F401

    def test_validate_manifest_invariants_importable(self):
        from maildir_report.manifest import validate_manifest_invariants  # noqa: F401

    def test_manifest_invariant_error_importable(self):
        from maildir_report.manifest import ManifestInvariantError  # noqa: F401

    def test_manifest_invariant_error_is_exception(self):
        from maildir_report.manifest import ManifestInvariantError

        assert issubclass(ManifestInvariantError, Exception)


# ── build_manifest: required top-level keys ───────────────────────────────────


class TestBuildManifestSchema:
    """build_manifest() must return a dict with all required schema keys."""

    def test_has_schema_version(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "schema_version" in m

    def test_schema_version_is_string(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert isinstance(m["schema_version"], str)

    def test_schema_version_value(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["schema_version"] == "1.0"

    def test_has_generated_at(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "generated_at" in m

    def test_generated_at_matches_timestamp(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["generated_at"] == GENERATED_AT

    def test_generated_at_is_utc_iso8601(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], "2024-12-31T23:59:59-05:00")
        # -05:00 normalised to UTC: 23:59:59 + 5h = 2025-01-01T04:59:59+00:00
        assert m["generated_at"] == "2025-01-01T04:59:59+00:00"

    def test_has_inventory(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "inventory" in m

    def test_inventory_is_dict(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert isinstance(m["inventory"], dict)

    def test_inventory_has_email_count(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "email_count" in m["inventory"]

    def test_inventory_has_dup_email_count(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "dup_email_count" in m["inventory"]

    def test_inventory_has_dup_group_count(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "dup_group_count" in m["inventory"]

    def test_inventory_has_total_size_bytes(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "total_size_bytes" in m["inventory"]

    def test_has_email_stable_ids(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "email_stable_ids" in m

    def test_email_stable_ids_is_list(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert isinstance(m["email_stable_ids"], list)

    def test_has_dup_groups(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "dup_groups" in m

    def test_dup_groups_is_list(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert isinstance(m["dup_groups"], list)

    def test_has_pdf_sha256(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert "pdf_sha256" in m

    def test_pdf_sha256_is_none_when_no_pdf(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["pdf_sha256"] is None

    def test_pdf_sha256_is_hex_when_pdf_bytes_provided(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS, pdf_bytes=b"fake pdf bytes")
        assert m["pdf_sha256"] is not None
        assert isinstance(m["pdf_sha256"], str)
        # Must be a 64-char hex SHA-256 digest
        assert len(m["pdf_sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in m["pdf_sha256"])

    def test_pdf_sha256_correct_value(self):
        from maildir_report.manifest import build_manifest

        payload = b"deterministic pdf"
        expected = hashlib.sha256(payload).hexdigest()
        m = build_manifest([], [], TS, pdf_bytes=payload)
        assert m["pdf_sha256"] == expected


# ── build_manifest: inventory counters ────────────────────────────────────────


class TestBuildManifestInventoryCounters:
    """Inventory counters must reflect the input records and dup groups."""

    def test_empty_records_email_count_zero(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["inventory"]["email_count"] == 0

    def test_empty_records_total_size_zero(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["inventory"]["total_size_bytes"] == 0

    def test_empty_records_dup_email_count_zero(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["inventory"]["dup_email_count"] == 0

    def test_empty_records_dup_group_count_zero(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["inventory"]["dup_group_count"] == 0

    def test_email_count_matches_records_length(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        n = 5
        for i in range(n):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(message_id=f"<m{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        assert m["inventory"]["email_count"] == n

    def test_total_size_bytes_matches_sum(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(message_id=f"<sz{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        expected_total = sum(r["total_size"] for r in records)
        m = build_manifest(records, dup_groups, TS)
        assert m["inventory"]["total_size_bytes"] == expected_total

    def test_dup_email_count_with_duplicates(self, tmp_path):
        """Emails in dup groups must be counted in dup_email_count."""
        from maildir_report.manifest import build_manifest

        # Create 3 emails that share an attachment (so they're all in one group)
        shared_payload = b"shared attachment content unique12345"
        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"dup{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<dup{i}@example.com>",
                    payload=shared_payload,
                    date=f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        assert m["inventory"]["dup_email_count"] == 3
        assert m["inventory"]["dup_group_count"] == 1

    def test_dup_email_count_zero_when_no_dups(self, tmp_path):
        """With no duplicates, dup_email_count must be 0."""
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        for i in range(3):
            # Distinct payloads → no duplicates
            (root / "cur" / f"unique{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<uniq{i}@example.com>",
                    payload=f"unique payload {i}".encode(),
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        assert m["inventory"]["dup_email_count"] == 0
        assert m["inventory"]["dup_group_count"] == 0


# ── build_manifest: email_stable_ids list ─────────────────────────────────────


class TestBuildManifestEmailStableIds:
    """email_stable_ids must contain exactly one ID per record in sort_emails() order."""

    def test_empty_records_empty_ids(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["email_stable_ids"] == []

    def test_count_matches_email_count(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        n = 4
        for i in range(n):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(message_id=f"<ids{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        assert len(m["email_stable_ids"]) == n

    def test_ids_are_strings(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        (root / "cur" / "m.msg").write_bytes(_simple_mail(message_id="<s@example.com>"))
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        assert all(isinstance(sid, str) for sid in m["email_stable_ids"])

    def test_ids_are_64_char_hex(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        (root / "cur" / "m.msg").write_bytes(
            _simple_mail(message_id="<hex@example.com>")
        )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        for sid in m["email_stable_ids"]:
            assert len(sid) == 64
            assert all(c in "0123456789abcdef" for c in sid)

    def test_ids_are_unique(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        for i in range(5):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(message_id=f"<unique{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        ids = m["email_stable_ids"]
        assert len(ids) == len(set(ids)), "email_stable_ids must not contain duplicates"

    def test_ids_match_record_stable_ids(self, tmp_path):
        from maildir_report.manifest import build_manifest
        from maildir_report.ordering import sort_emails

        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(
                    message_id=f"<match{i}@example.com>",
                    date=f"Mon, 0{i + 1} Jan 2024 08:00:00 +0000",
                )
            )
        records, dup_groups = _scan_and_group(root)
        expected_ids = [r["stable_id"] for r in sort_emails(records)]
        m = build_manifest(records, dup_groups, TS)
        assert m["email_stable_ids"] == expected_ids

    def test_ids_deterministic_across_calls(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(message_id=f"<det{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m1 = build_manifest(records, dup_groups, TS)
        m2 = build_manifest(records, dup_groups, TS)
        assert m1["email_stable_ids"] == m2["email_stable_ids"]


# ── build_manifest: dup_groups list ───────────────────────────────────────────


class TestBuildManifestDupGroups:
    """dup_groups list must contain required fields in sort_dup_groups() order."""

    def test_empty_dup_groups_empty_list(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        assert m["dup_groups"] == []

    def test_dup_group_has_required_fields(self, tmp_path):
        from maildir_report.manifest import build_manifest

        shared = b"shared content for dup group test abc"
        root = _make_maildir(tmp_path)
        for i in range(2):
            (root / "cur" / f"d{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<dg{i}@example.com>",
                    payload=shared,
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)

        assert len(m["dup_groups"]) == 1
        g = m["dup_groups"][0]
        for key in (
            "group_id",
            "member_count",
            "member_email_ids",
            "canonical_email_id",
            "total_size_bytes",
        ):
            assert key in g, f"dup_group entry missing key: {key!r}"

    def test_dup_group_member_count_correct(self, tmp_path):
        from maildir_report.manifest import build_manifest

        shared = b"shared binary attachment for counting"
        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"cnt{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<cnt{i}@example.com>",
                    payload=shared,
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        assert m["dup_groups"][0]["member_count"] == 3

    def test_dup_group_member_email_ids_are_list_of_strings(self, tmp_path):
        from maildir_report.manifest import build_manifest

        shared = b"member_ids_test_payload_xyz789"
        root = _make_maildir(tmp_path)
        for i in range(2):
            (root / "cur" / f"mid{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<mid{i}@example.com>",
                    payload=shared,
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        member_ids = m["dup_groups"][0]["member_email_ids"]
        assert isinstance(member_ids, list)
        assert all(isinstance(sid, str) for sid in member_ids)

    def test_dup_group_group_id_is_64_char_hex(self, tmp_path):
        from maildir_report.manifest import build_manifest

        shared = b"group_id_hex_test_content_lmn456"
        root = _make_maildir(tmp_path)
        for i in range(2):
            (root / "cur" / f"gid{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<gid{i}@example.com>",
                    payload=shared,
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        gid = m["dup_groups"][0]["group_id"]
        assert len(gid) == 64
        assert all(c in "0123456789abcdef" for c in gid)

    def test_dup_groups_deterministic_across_calls(self, tmp_path):
        from maildir_report.manifest import build_manifest

        shared = b"deterministic_dup_groups_payload_qrs"
        root = _make_maildir(tmp_path)
        for i in range(2):
            (root / "cur" / f"det{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<det{i}@example.com>",
                    payload=shared,
                )
            )
        records, dup_groups = _scan_and_group(root)
        m1 = build_manifest(records, dup_groups, TS)
        m2 = build_manifest(records, dup_groups, TS)
        assert m1["dup_groups"] == m2["dup_groups"]


# ── build_manifest: JSON serialisability ──────────────────────────────────────


class TestBuildManifestJsonSerializable:
    """build_manifest() must return a JSON-serialisable dict (no bytes, no datetime)."""

    def test_empty_manifest_json_serializable(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS)
        # Must not raise
        serialized = json.dumps(m)
        assert isinstance(serialized, str)

    def test_manifest_with_records_json_serializable(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(message_id=f"<json{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        serialized = json.dumps(m)
        # Round-trip: parsed back should equal the dict
        assert json.loads(serialized) == m

    def test_manifest_with_pdf_bytes_json_serializable(self):
        from maildir_report.manifest import build_manifest

        m = build_manifest([], [], TS, pdf_bytes=b"%PDF-1.4 fake content")
        serialized = json.dumps(m)
        restored = json.loads(serialized)
        assert restored["pdf_sha256"] == m["pdf_sha256"]

    def test_manifest_json_round_trip_with_dups(self, tmp_path):
        from maildir_report.manifest import build_manifest

        shared = b"json_round_trip_shared_payload_abc"
        root = _make_maildir(tmp_path)
        for i in range(2):
            (root / "cur" / f"jrt{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<jrt{i}@example.com>",
                    payload=shared,
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        restored = json.loads(json.dumps(m))
        assert restored == m


# ── validate_manifest_invariants: happy path ─────────────────────────────────


class TestValidateManifestInvariantsHappy:
    """validate_manifest_invariants() must return None silently when all invariants hold."""

    def test_empty_manifest_passes(self):
        from maildir_report.manifest import build_manifest, validate_manifest_invariants

        m = build_manifest([], [], TS)
        result = validate_manifest_invariants(m)
        assert result is None

    def test_manifest_with_records_passes(self, tmp_path):
        from maildir_report.manifest import build_manifest, validate_manifest_invariants

        root = _make_maildir(tmp_path)
        for i in range(5):
            (root / "cur" / f"v{i}.msg").write_bytes(
                _simple_mail(message_id=f"<val{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        result = validate_manifest_invariants(m)
        assert result is None

    def test_manifest_with_dups_passes(self, tmp_path):
        from maildir_report.manifest import build_manifest, validate_manifest_invariants

        shared = b"invariant_test_shared_payload_xyz"
        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"inv{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<inv{i}@example.com>",
                    payload=shared,
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)
        result = validate_manifest_invariants(m)
        assert result is None

    def test_manifest_with_pdf_sha256_passes(self):
        from maildir_report.manifest import build_manifest, validate_manifest_invariants

        m = build_manifest([], [], TS, pdf_bytes=b"some pdf bytes")
        result = validate_manifest_invariants(m)
        assert result is None


# ── validate_manifest_invariants: violation detection ────────────────────────


class TestValidateManifestInvariantsViolations:
    """validate_manifest_invariants() must raise ManifestInvariantError on any violation."""

    def test_email_count_mismatch_raises(self):
        from maildir_report.manifest import (
            ManifestInvariantError,
            build_manifest,
            validate_manifest_invariants,
        )

        m = build_manifest([], [], TS)
        # Tamper: add a fake ID without updating email_count
        m["email_stable_ids"].append("a" * 64)
        # email_count=0 but len(email_stable_ids)=1 — invariant 1 violated

        with pytest.raises(ManifestInvariantError) as exc_info:
            validate_manifest_invariants(m)
        assert "email_count" in str(exc_info.value)

    def test_dup_email_count_exceeds_email_count_raises(self):
        from maildir_report.manifest import (
            ManifestInvariantError,
            build_manifest,
            validate_manifest_invariants,
        )

        m = build_manifest([], [], TS)
        # Tamper: dup_email_count > email_count
        m["inventory"]["dup_email_count"] = 5  # email_count is 0
        with pytest.raises(ManifestInvariantError) as exc_info:
            validate_manifest_invariants(m)
        assert "dup_email_count" in str(exc_info.value)

    def test_dup_group_count_mismatch_raises(self):
        from maildir_report.manifest import (
            ManifestInvariantError,
            build_manifest,
            validate_manifest_invariants,
        )

        m = build_manifest([], [], TS)
        # Tamper: add a fake group without updating dup_group_count
        m["dup_groups"].append(
            {
                "group_id": "b" * 64,
                "member_count": 2,
                "member_email_ids": ["c" * 64, "d" * 64],
                "canonical_email_id": "c" * 64,
                "total_size_bytes": 100,
            }
        )
        # dup_group_count=0, len(dup_groups)=1 — invariant 3 violated
        with pytest.raises(ManifestInvariantError) as exc_info:
            validate_manifest_invariants(m)
        assert "dup_group_count" in str(exc_info.value)

    def test_sum_member_count_mismatch_raises(self):
        from maildir_report.manifest import (
            ManifestInvariantError,
            validate_manifest_invariants,
        )

        # Build a manifest that has dup_group_count==1 and dup_email_count==2
        # but group member_count==3 → invariant 4 violated
        manifest = {
            "schema_version": "1.0",
            "generated_at": GENERATED_AT,
            "inventory": {
                "email_count": 3,
                "dup_email_count": 2,  # <- 2 emails in dups
                "dup_group_count": 1,
                "total_size_bytes": 0,
            },
            "email_stable_ids": ["a" * 64, "b" * 64, "c" * 64],
            "dup_groups": [
                {
                    "group_id": "d" * 64,
                    "member_count": 3,  # <- but group says 3 members
                    "member_email_ids": ["a" * 64, "b" * 64, "c" * 64],
                    "canonical_email_id": "a" * 64,
                    "total_size_bytes": 0,
                }
            ],
            "pdf_sha256": None,
        }
        with pytest.raises(ManifestInvariantError) as exc_info:
            validate_manifest_invariants(manifest)
        # sum(member_count)=3 != dup_email_count=2
        assert "member_count" in str(exc_info.value) or "dup_email_count" in str(
            exc_info.value
        )

    def test_duplicate_email_stable_ids_raises(self):
        from maildir_report.manifest import (
            ManifestInvariantError,
            validate_manifest_invariants,
        )

        manifest = {
            "schema_version": "1.0",
            "generated_at": GENERATED_AT,
            "inventory": {
                "email_count": 2,
                "dup_email_count": 0,
                "dup_group_count": 0,
                "total_size_bytes": 0,
            },
            "email_stable_ids": ["a" * 64, "a" * 64],  # <- duplicate!
            "dup_groups": [],
            "pdf_sha256": None,
        }
        with pytest.raises(ManifestInvariantError) as exc_info:
            validate_manifest_invariants(manifest)
        assert "duplicate" in str(exc_info.value).lower()

    def test_duplicate_group_ids_raises(self):
        from maildir_report.manifest import (
            ManifestInvariantError,
            validate_manifest_invariants,
        )

        manifest = {
            "schema_version": "1.0",
            "generated_at": GENERATED_AT,
            "inventory": {
                "email_count": 4,
                "dup_email_count": 4,
                "dup_group_count": 2,  # 2 groups
                "total_size_bytes": 0,
            },
            "email_stable_ids": ["a" * 64, "b" * 64, "c" * 64, "d" * 64],
            "dup_groups": [
                {
                    "group_id": "x" * 64,  # <- same group_id in both entries
                    "member_count": 2,
                    "member_email_ids": ["a" * 64, "b" * 64],
                    "canonical_email_id": "a" * 64,
                    "total_size_bytes": 0,
                },
                {
                    "group_id": "x" * 64,  # <- duplicate!
                    "member_count": 2,
                    "member_email_ids": ["c" * 64, "d" * 64],
                    "canonical_email_id": "c" * 64,
                    "total_size_bytes": 0,
                },
            ],
            "pdf_sha256": None,
        }
        with pytest.raises(ManifestInvariantError) as exc_info:
            validate_manifest_invariants(manifest)
        assert "duplicate" in str(exc_info.value).lower()

    def test_violations_attribute_is_list(self):
        from maildir_report.manifest import (
            ManifestInvariantError,
            build_manifest,
            validate_manifest_invariants,
        )

        m = build_manifest([], [], TS)
        m["inventory"]["email_count"] = 99  # bad value
        with pytest.raises(ManifestInvariantError) as exc_info:
            validate_manifest_invariants(m)
        assert isinstance(exc_info.value.violations, list)
        assert len(exc_info.value.violations) >= 1


# ── build_manifest: determinism ───────────────────────────────────────────────


class TestBuildManifestDeterminism:
    """Same inputs must always produce equal manifest dicts."""

    def test_two_calls_produce_equal_manifest(self, tmp_path):
        from maildir_report.manifest import build_manifest

        shared = b"determinism_test_payload_abcxyz"
        root = _make_maildir(tmp_path)
        for i in range(4):
            (root / "cur" / f"d{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<det{i}@example.com>",
                    payload=shared if i < 2 else f"unique_payload_{i}".encode(),
                )
            )
        records, dup_groups = _scan_and_group(root)

        pdf_b = b"some pdf bytes for sha256"
        m1 = build_manifest(records, dup_groups, TS, pdf_bytes=pdf_b)
        m2 = build_manifest(records, dup_groups, TS, pdf_bytes=pdf_b)
        assert m1 == m2

    def test_manifest_json_identical_across_calls(self, tmp_path):
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        for i in range(3):
            (root / "cur" / f"m{i}.msg").write_bytes(
                _simple_mail(message_id=f"<j{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m1 = build_manifest(records, dup_groups, TS)
        m2 = build_manifest(records, dup_groups, TS)
        assert json.dumps(m1, sort_keys=True) == json.dumps(m2, sort_keys=True)

    def test_different_timestamps_produce_different_generated_at(self):
        from maildir_report.manifest import build_manifest

        m1 = build_manifest([], [], "2024-01-01T00:00:00+00:00")
        m2 = build_manifest([], [], "2024-12-31T23:59:59+00:00")
        assert m1["generated_at"] != m2["generated_at"]


# ── integration: 47-file fixture ──────────────────────────────────────────────


class TestManifestIntegration:
    """Integration tests: scan + group_emails + build_manifest + validate."""

    def test_47_files_manifest_passes_validation(self, tmp_path):
        """47-file fixture: manifest counters and invariants must all hold."""
        from maildir_report.manifest import build_manifest, validate_manifest_invariants

        root = _make_maildir(tmp_path)
        n = 47
        for i in range(n):
            (root / "cur" / f"m{i:04d}.msg").write_bytes(
                _simple_mail(
                    message_id=f"<m47_{i}@example.com>",
                    subject=f"Message {i}",
                )
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)

        assert m["inventory"]["email_count"] == n
        assert len(m["email_stable_ids"]) == n
        # No dups → dup counters must be 0
        assert m["inventory"]["dup_email_count"] == 0
        assert m["inventory"]["dup_group_count"] == 0
        assert m["dup_groups"] == []
        # Invariants must pass
        validate_manifest_invariants(m)

    def test_manifest_with_mixed_dup_and_unique_passes(self, tmp_path):
        """Mixed maildir: 2 dup emails + 2 unique → manifest correct."""
        from maildir_report.manifest import build_manifest, validate_manifest_invariants

        shared = b"shared_attachment_for_integration_test"
        root = _make_maildir(tmp_path)
        # Two emails with shared attachment
        for i in range(2):
            (root / "cur" / f"dup{i}.msg").write_bytes(
                _mail_with_attachment(
                    message_id=f"<dup{i}@example.com>",
                    payload=shared,
                )
            )
        # Two unique emails
        for i in range(2):
            (root / "cur" / f"uniq{i}.msg").write_bytes(
                _simple_mail(message_id=f"<uniq{i}@example.com>")
            )
        records, dup_groups = _scan_and_group(root)
        m = build_manifest(records, dup_groups, TS)

        assert m["inventory"]["email_count"] == 4
        assert m["inventory"]["dup_email_count"] == 2
        assert m["inventory"]["dup_group_count"] == 1
        assert len(m["email_stable_ids"]) == 4
        assert len(m["dup_groups"]) == 1
        assert m["dup_groups"][0]["member_count"] == 2
        validate_manifest_invariants(m)

    def test_pdf_sha256_matches_provided_bytes(self, tmp_path):
        """pdf_sha256 field must match the SHA-256 of the supplied pdf_bytes."""
        from maildir_report.manifest import build_manifest

        root = _make_maildir(tmp_path)
        (root / "cur" / "m.msg").write_bytes(
            _simple_mail(message_id="<pdf@example.com>")
        )
        records, dup_groups = _scan_and_group(root)

        fake_pdf = b"%PDF-1.4 fake deterministic content"
        m = build_manifest(records, dup_groups, TS, pdf_bytes=fake_pdf)
        expected_sha = hashlib.sha256(fake_pdf).hexdigest()
        assert m["pdf_sha256"] == expected_sha

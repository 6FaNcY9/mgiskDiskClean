"""
test_apply_decisions.py — Tests for maildir_report.apply_decisions (Task 12).

Test contracts
--------------
test_plan_stable_hash
    Calling plan twice on the same decisions CSV and Maildir produces an
    identical candidate_set_hash (deterministic ordering).

test_apply_quarantines_only_delete
    After `apply`, only files whose decisions row says "delete" are moved to
    the quarantine; rows with other decisions (keep, blank, etc.) are left alone.

test_apply_traversal_rejected
    A tampered plan.json that points a candidate filepath outside maildir_root
    must be rejected (exit code != 0) before any file is moved.

test_restore
    After apply, calling restore moves all quarantined files back to their
    original paths.

test_cap_enforced
    When the plan has more than 10,000 candidates and --break-glass is NOT set,
    the plan subcommand exits with a nonzero code.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import pytest


# ── helpers ────────────────────────────────────────────────────────────────────


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_eml(content: str = "") -> bytes:
    """Return minimal .eml content as bytes."""
    body = content or "Test body."
    return (
        f"From: sender@example.com\r\n"
        f"To: recipient@example.com\r\n"
        f"Subject: Test message\r\n"
        f"Message-ID: <test-{uuid.uuid4()}@example.com>\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def _make_maildir(tmp_path: Path) -> Path:
    """Create a minimal Maildir structure and return the maildir root."""
    maildir = tmp_path / "maildir"
    (maildir / "cur").mkdir(parents=True)
    (maildir / "new").mkdir(parents=True)
    (maildir / "tmp").mkdir(parents=True)
    return maildir


def _write_mail_file(
    directory: Path, filename: str, content: bytes | None = None
) -> Path:
    """Write a mail file and return its path."""
    path = directory / filename
    path.write_bytes(content or _make_eml())
    return path


def _write_decisions_csv(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    """Write a minimal decisions CSV to *path*."""
    # Use the canonical header list from decisions_template
    headers = [
        "stable_id",
        "filepath",
        "decision",
        "folder",
        "date",
        "from",
        "subject",
        "total_size_bytes",
        "attachment_count",
        "attachment_total_bytes",
        "attachment_names",
        "is_duplicate",
        "dup_group_id",
        "dup_rank",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Fill missing fields with empty strings
            full_row = {h: row.get(h, "") for h in headers}
            writer.writerow(full_row)


def _make_csv_row(
    filepath: str,
    decision: str = "delete",
    stable_id: str | None = None,
) -> dict[str, str]:
    """Return a minimal decisions-CSV row dict."""
    sid = stable_id or ("a" * 64)
    return {
        "stable_id": sid,
        "filepath": filepath,
        "decision": decision,
        "folder": "INBOX",
        "date": "2024-01-01 10:00",
        "from": "sender@example.com",
        "subject": "Test",
        "total_size_bytes": "100",
        "attachment_count": "0",
        "attachment_total_bytes": "0",
        "attachment_names": "",
        "is_duplicate": "false",
        "dup_group_id": "",
        "dup_rank": "",
    }


def _run_plan(
    maildir_root: Path,
    decisions_csv: Path,
    output_plan: Path,
    extra_argv: list[str] | None = None,
) -> int:
    """Run the plan subcommand and return the exit code."""
    from maildir_report.apply_decisions import main

    argv = [
        "plan",
        "--maildir-root",
        str(maildir_root),
        "--decisions-csv",
        str(decisions_csv),
        "--output-plan",
        str(output_plan),
    ] + (extra_argv or [])
    return main(argv)


def _run_apply(
    plan_path: Path,
    confirm: str,
    extra_argv: list[str] | None = None,
) -> int:
    """Run the apply subcommand and return the exit code."""
    from maildir_report.apply_decisions import main

    argv = [
        "apply",
        "--plan",
        str(plan_path),
        "--confirm",
        confirm,
    ] + (extra_argv or [])
    return main(argv)


def _run_restore(
    plan_id: str,
    maildir_root: Path,
    extra_argv: list[str] | None = None,
) -> int:
    """Run the restore subcommand and return the exit code."""
    from maildir_report.apply_decisions import main

    argv = [
        "restore",
        "--plan-id",
        plan_id,
        "--maildir-root",
        str(maildir_root),
    ] + (extra_argv or [])
    return main(argv)


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def maildir(tmp_path: Path) -> Path:
    """A fresh Maildir with three .eml files: two in cur/, one in new/."""
    md = _make_maildir(tmp_path)
    _write_mail_file(md / "cur", "mail_delete.eml")
    _write_mail_file(md / "cur", "mail_keep.eml")
    _write_mail_file(md / "new", "mail_new_delete.eml")
    return md


@pytest.fixture()
def decisions_csv_path(tmp_path: Path, maildir: Path) -> Path:
    """A decisions CSV with:
    - mail_delete.eml → decision=delete
    - mail_keep.eml   → decision=keep
    - mail_new_delete.eml → decision=delete
    """
    delete_file_1 = maildir / "cur" / "mail_delete.eml"
    keep_file = maildir / "cur" / "mail_keep.eml"
    delete_file_2 = maildir / "new" / "mail_new_delete.eml"

    rows = [
        _make_csv_row(str(delete_file_1), decision="delete", stable_id="d" * 64),
        _make_csv_row(str(keep_file), decision="keep", stable_id="k" * 64),
        _make_csv_row(str(delete_file_2), decision="delete", stable_id="n" * 64),
    ]
    csv_path = tmp_path / "decisions.reviewed.csv"
    _write_decisions_csv(csv_path, rows)
    return csv_path


# ── tests ──────────────────────────────────────────────────────────────────────


class TestPlanStableHash:
    """plan produces identical candidate_set_hash on two consecutive runs."""

    def test_plan_stable_hash(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_1 = tmp_path / "plan1.json"
        plan_2 = tmp_path / "plan2.json"

        rc1 = _run_plan(maildir, decisions_csv_path, plan_1)
        rc2 = _run_plan(maildir, decisions_csv_path, plan_2)

        assert rc1 == 0, f"First plan run returned exit code {rc1}"
        assert rc2 == 0, f"Second plan run returned exit code {rc2}"

        data1 = json.loads(plan_1.read_text(encoding="utf-8"))
        data2 = json.loads(plan_2.read_text(encoding="utf-8"))

        assert data1["candidate_set_hash"] == data2["candidate_set_hash"], (
            f"Hashes differ: {data1['candidate_set_hash']!r} vs {data2['candidate_set_hash']!r}"
        )

    def test_plan_candidates_sorted_by_stable_id(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        """Candidates in plan file must be sorted by stable_id."""
        plan_path = tmp_path / "plan.json"
        rc = _run_plan(maildir, decisions_csv_path, plan_path)
        assert rc == 0
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        ids = [c["stable_id"] for c in data["candidates"]]
        assert ids == sorted(ids)


class TestApplyQuarantinesOnlyDelete:
    """apply moves only decision=delete files; keep/other rows are untouched."""

    def test_apply_quarantines_only_delete(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        rc_plan = _run_plan(maildir, decisions_csv_path, plan_path)
        assert rc_plan == 0

        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        confirm = plan_data["candidate_set_hash"][:8]
        plan_id = plan_data["plan_id"]

        rc_apply = _run_apply(plan_path, confirm)
        assert rc_apply == 0

        # delete files must be in quarantine
        q_base = maildir / ".quarantine" / plan_id
        q_del1 = q_base / "cur" / "mail_delete.eml"
        q_del2 = q_base / "new" / "mail_new_delete.eml"
        assert q_del1.exists(), f"Expected quarantined file at {q_del1}"
        assert q_del2.exists(), f"Expected quarantined file at {q_del2}"

        # original delete files must be gone
        assert not (maildir / "cur" / "mail_delete.eml").exists()
        assert not (maildir / "new" / "mail_new_delete.eml").exists()

        # keep file must still be in place
        assert (maildir / "cur" / "mail_keep.eml").exists()

    def test_audit_jsonl_written(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        confirm = plan_data["candidate_set_hash"][:8]
        plan_id = plan_data["plan_id"]
        _run_apply(plan_path, confirm)

        audit_path = maildir / ".quarantine" / plan_id / "audit.jsonl"
        assert audit_path.exists(), f"audit.jsonl not found at {audit_path}"

        entries = []
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        assert len(entries) == 2  # two delete rows


class TestApplyTraversalRejected:
    """apply must reject filepaths outside maildir_root."""

    def test_apply_traversal_rejected(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))

        # Create a file OUTSIDE maildir_root
        evil_file = tmp_path / "outside_maildir" / "evil.eml"
        evil_file.parent.mkdir(parents=True, exist_ok=True)
        evil_file.write_bytes(b"evil content")

        # Tamper the plan: replace the first candidate's filepath with an outside path
        if plan_data["candidates"]:
            plan_data["candidates"][0]["filepath"] = str(evil_file)
        # Write tampered plan
        tampered_plan = tmp_path / "tampered_plan.json"
        tampered_plan.write_text(json.dumps(plan_data, indent=2), encoding="utf-8")

        confirm = plan_data["candidate_set_hash"][:8]

        rc = _run_apply(tampered_plan, confirm)
        assert rc != 0, "Expected nonzero exit when traversal path detected"

        # The evil file must NOT have been touched
        assert evil_file.exists(), "Evil file should not have been moved/deleted"

    def test_traversal_with_dotdot_path(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        """Paths using ../.. that resolve outside maildir_root must be rejected."""
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))

        # Craft a dotdot path that resolves outside maildir
        evil_file = tmp_path / "etc_passwd.eml"
        evil_file.write_bytes(b"root:x:0:0")

        if plan_data["candidates"]:
            plan_data["candidates"][0]["filepath"] = str(
                maildir / "cur" / ".." / ".." / "etc_passwd.eml"
            )

        tampered_plan = tmp_path / "tampered_dotdot.json"
        tampered_plan.write_text(json.dumps(plan_data, indent=2), encoding="utf-8")
        confirm = plan_data["candidate_set_hash"][:8]

        rc = _run_apply(tampered_plan, confirm)
        assert rc != 0


class TestRestore:
    """restore moves quarantined files back to their original paths."""

    def test_restore(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        confirm = plan_data["candidate_set_hash"][:8]
        plan_id = plan_data["plan_id"]

        # Apply
        _run_apply(plan_path, confirm)

        # Confirm files are in quarantine
        assert not (maildir / "cur" / "mail_delete.eml").exists()
        assert not (maildir / "new" / "mail_new_delete.eml").exists()

        # Restore
        rc_restore = _run_restore(plan_id, maildir)
        assert rc_restore == 0, f"restore returned exit code {rc_restore}"

        # Original files must be back
        assert (maildir / "cur" / "mail_delete.eml").exists()
        assert (maildir / "new" / "mail_new_delete.eml").exists()

    def test_restore_dry_run_does_not_move(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        confirm = plan_data["candidate_set_hash"][:8]
        plan_id = plan_data["plan_id"]

        _run_apply(plan_path, confirm)

        # Dry-run restore must NOT move anything
        rc = _run_restore(plan_id, maildir, extra_argv=["--dry-run"])
        assert rc == 0

        # Files must still be in quarantine
        assert not (maildir / "cur" / "mail_delete.eml").exists()
        assert not (maildir / "new" / "mail_new_delete.eml").exists()


class TestCapEnforced:
    """plan exits nonzero when candidates > 10,000 without --break-glass."""

    def test_cap_enforced(self, tmp_path: Path, maildir: Path) -> None:
        """Mocking 10,001 candidates forces cap rejection without --break-glass."""
        from maildir_report.apply_decisions import _FILE_CAP, cmd_plan

        decisions_csv = tmp_path / "decisions.csv"
        # Single real file to avoid 'no file found' early-exit
        mail = maildir / "cur" / "mail_delete.eml"

        rows = [_make_csv_row(str(mail), decision="delete", stable_id="a" * 64)]
        _write_decisions_csv(decisions_csv, rows)

        # Patch so that the single resolved candidate becomes 10,001 after gathering
        # by building a patched _read_decisions_csv that returns many rows.
        many_rows: list[dict[str, str]] = []
        for i in range(_FILE_CAP + 1):
            many_rows.append(
                _make_csv_row(
                    str(mail),
                    decision="delete",
                    stable_id=f"{i:064x}",
                )
            )

        plan_out = tmp_path / "plan.json"

        with mock.patch(
            "maildir_report.apply_decisions._read_decisions_csv",
            return_value=many_rows,
        ):
            # Also patch os.path.isfile to return True for all paths
            with mock.patch("os.path.isfile", return_value=True):
                with mock.patch("os.path.getsize", return_value=100):
                    args = argparse.Namespace(
                        maildir_root=str(maildir),
                        decisions_csv=str(decisions_csv),
                        mode="quarantine",
                        dry_run=False,
                        break_glass=False,
                        output_plan=str(plan_out),
                    )
                    rc = cmd_plan(args)

        assert rc != 0, "Expected nonzero exit when cap exceeded"

    def test_cap_lifted_with_break_glass(self, tmp_path: Path, maildir: Path) -> None:
        """--break-glass allows > 10,000 candidates."""
        from maildir_report.apply_decisions import _FILE_CAP, cmd_plan

        mail = maildir / "cur" / "mail_delete.eml"

        many_rows: list[dict[str, str]] = []
        for i in range(_FILE_CAP + 1):
            many_rows.append(
                _make_csv_row(
                    str(mail),
                    decision="delete",
                    stable_id=f"{i:064x}",
                )
            )

        plan_out = tmp_path / "plan.json"

        with mock.patch(
            "maildir_report.apply_decisions._read_decisions_csv",
            return_value=many_rows,
        ):
            with mock.patch("os.path.isfile", return_value=True):
                with mock.patch("os.path.getsize", return_value=100):
                    args = argparse.Namespace(
                        maildir_root=str(maildir),
                        decisions_csv=str(tmp_path / "dummy.csv"),
                        mode="quarantine",
                        dry_run=False,
                        break_glass=True,  # <-- break-glass
                        output_plan=str(plan_out),
                    )
                    rc = cmd_plan(args)

        assert rc == 0, "Expected zero exit with --break-glass"


class TestPlanDryRun:
    """plan --dry-run prints summary AND writes a plan artifact (preview-then-execute)."""

    def test_plan_dry_run_writes_file(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        """--dry-run must still write the plan JSON (plan.764: 'writes a plan file')."""
        plan_path = tmp_path / "dry_run_plan.json"
        rc = _run_plan(maildir, decisions_csv_path, plan_path, extra_argv=["--dry-run"])
        assert rc == 0
        assert plan_path.exists(), "Plan file MUST be written even on --dry-run"
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        assert "candidate_set_hash" in data
        assert "candidates" in data
        assert len(data["candidates"]) == 2  # only delete rows

    def test_plan_dry_run_hash_stable(self, tmp_path: Path, maildir: Path, decisions_csv_path: Path) -> None:
        """--dry-run plan file has same hash as non-dry-run plan file."""
        dry = tmp_path / "dry.json"
        wet = tmp_path / "wet.json"
        _run_plan(maildir, decisions_csv_path, dry, extra_argv=["--dry-run"])
        _run_plan(maildir, decisions_csv_path, wet)
        d1 = json.loads(dry.read_text(encoding="utf-8"))
        d2 = json.loads(wet.read_text(encoding="utf-8"))
        assert d1["candidate_set_hash"] == d2["candidate_set_hash"]

class TestApplyDryRun:
    """apply --dry-run prints moves but does NOT move files."""

    def test_apply_dry_run_no_move(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        confirm = plan_data["candidate_set_hash"][:8]

        rc = _run_apply(plan_path, confirm, extra_argv=["--dry-run"])
        assert rc == 0

        # Original files must still exist
        assert (maildir / "cur" / "mail_delete.eml").exists()
        assert (maildir / "new" / "mail_new_delete.eml").exists()

        # No quarantine dir should have been created
        quarantine_plan_dir = maildir / ".quarantine" / plan_data["plan_id"]
        assert not quarantine_plan_dir.exists()


class TestConfirmHashPrefix:
    """apply --confirm must match the candidate_set_hash prefix."""

    def test_wrong_confirm_rejected(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)

        rc = _run_apply(plan_path, "00000000")  # wrong prefix
        assert rc != 0

    def test_short_confirm_rejected(
        self, tmp_path: Path, maildir: Path, decisions_csv_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)

        rc = _run_apply(plan_path, "abc")  # only 3 chars — below minimum
        assert rc != 0


class TestHelp:
    """--help must exit 0."""

    def test_help_exits_zero(self) -> None:
        from maildir_report.apply_decisions import _build_parser

        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_plan_help_exits_zero(self) -> None:
        from maildir_report.apply_decisions import _build_parser

        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["plan", "--help"])
        assert exc_info.value.code == 0


class TestApplyModeDelete:
    """apply respects plan mode=delete: files are deleted, not quarantined."""

    def test_apply_mode_delete_removes_file(self, tmp_path: Path, maildir: Path, decisions_csv_path: Path) -> None:
        plan_path = tmp_path / "plan_delete.json"
        rc = _run_plan(maildir, decisions_csv_path, plan_path, extra_argv=["--mode", "delete"])
        assert rc == 0
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        assert plan_data["mode"] == "delete"
        confirm = plan_data["candidate_set_hash"][:8]

        rc_apply = _run_apply(plan_path, confirm)
        assert rc_apply == 0

        # Files must be gone entirely — not quarantined
        assert not (maildir / "cur" / "mail_delete.eml").exists()
        assert not (maildir / "new" / "mail_new_delete.eml").exists()

        # keep file must be untouched
        assert (maildir / "cur" / "mail_keep.eml").exists()

        # quarantine directory must NOT exist (mode=delete, not quarantine)
        q_base = maildir / ".quarantine" / plan_data["plan_id"]
        assert not q_base.exists(), "quarantine dir must not be created in mode=delete"

    def test_apply_mode_quarantine_moves_not_deletes(self, tmp_path: Path, maildir: Path, decisions_csv_path: Path) -> None:
        """mode=quarantine must MOVE (not delete) files."""
        plan_path = tmp_path / "plan_q.json"
        _run_plan(maildir, decisions_csv_path, plan_path)  # default mode=quarantine
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        assert plan_data["mode"] == "quarantine"
        confirm = plan_data["candidate_set_hash"][:8]
        plan_id = plan_data["plan_id"]

        _run_apply(plan_path, confirm)

        q_base = maildir / ".quarantine" / plan_id
        assert (q_base / "cur" / "mail_delete.eml").exists(), "file must be in quarantine"
        assert (q_base / "new" / "mail_new_delete.eml").exists(), "file must be in quarantine"


class TestAuditAppendOnly:
    """audit.jsonl must be append-only across repeated apply runs."""

    def test_audit_append_on_reapply(self, tmp_path: Path, maildir: Path, decisions_csv_path: Path) -> None:
        """Re-applying the same plan appends to existing audit.jsonl, never overwrites."""
        plan_path = tmp_path / "plan.json"
        _run_plan(maildir, decisions_csv_path, plan_path)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        confirm = plan_data["candidate_set_hash"][:8]
        plan_id = plan_data["plan_id"]

        # First apply: 2 files moved → 2 audit entries
        _run_apply(plan_path, confirm)
        audit_path = maildir / ".quarantine" / plan_id / "audit.jsonl"
        lines_after_first = [l for l in audit_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines_after_first) == 2, f"Expected 2 entries after first apply, got {len(lines_after_first)}"

        # Second apply (idempotent): 2 files already quarantined → 2 more 'already_quarantined' entries appended
        _run_apply(plan_path, confirm)
        lines_after_second = [l for l in audit_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines_after_second) == 4, (
            f"Expected 4 entries after second apply (append-only), got {len(lines_after_second)}. "
            "audit.jsonl may have been overwritten instead of appended."
        )

        # All entries must be valid JSON
        for line in lines_after_second:
            entry = json.loads(line)  # raises if malformed
            assert "stable_id" in entry

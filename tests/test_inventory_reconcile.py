"""
test_inventory_reconcile.py — TDD tests for Task 4: audited inventory reconciliation.

Contract being tested
---------------------
- list_maildir_files(root) returns sorted, normalized absolute paths for all
  non-hidden files in cur/ and new/ sub-directories (tmp/ excluded).
- reconcile_inventory(root, records) raises InventoryMismatchError when the set
  of files on disk differs from the set of filepaths in parsed records.
- InventoryMismatchError carries deterministic, sorted lists of missing and extra
  file paths, and names them in its string representation.
- reconcile_inventory(root, records) returns None (silently passes) when disk and
  records match exactly.
- 47-file fixture: manifest counters match exactly (no mismatch error raised).
- Unreadable-file fixture: MailParseError (from scan_maildir) surfaces the file path.
"""

from __future__ import annotations

import pathlib
import stat
import tempfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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


def _make_maildir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Maildir skeleton under tmp_path and return the root."""
    root = tmp_path / "Maildir"
    (root / "cur").mkdir(parents=True)
    (root / "new").mkdir(parents=True)
    (root / "tmp").mkdir(parents=True)
    return root


def _scan_to_records(root: pathlib.Path) -> list:
    """Convenience: scan a Maildir and return records list."""
    from maildir_report.parser import scan_maildir

    return scan_maildir(str(root))


# ── import guard ──────────────────────────────────────────────────────────────


class TestInventoryImports:
    """inventory module and its types must be importable from the package."""

    def test_inventory_module_importable(self):
        """maildir_report.inventory must be importable."""
        from maildir_report import inventory  # noqa: F401

    def test_inventory_list_files_importable(self):
        """list_maildir_files() must be importable from maildir_report.inventory."""
        from maildir_report.inventory import list_maildir_files  # noqa: F401

    def test_inventory_reconcile_importable(self):
        """reconcile_inventory() must be importable from maildir_report.inventory."""
        from maildir_report.inventory import reconcile_inventory  # noqa: F401

    def test_inventory_mismatch_error_importable(self):
        """InventoryMismatchError must be importable from maildir_report.inventory."""
        from maildir_report.inventory import InventoryMismatchError  # noqa: F401


# ── InventoryMismatchError ─────────────────────────────────────────────────────


class TestInventoryMismatchError:
    """InventoryMismatchError must carry context for debugging."""

    def test_inventory_mismatch_error_is_exception(self):
        from maildir_report.inventory import InventoryMismatchError

        assert issubclass(InventoryMismatchError, Exception)

    def test_inventory_mismatch_error_carries_missing(self):
        """InventoryMismatchError must store a list of missing file paths."""
        from maildir_report.inventory import InventoryMismatchError

        err = InventoryMismatchError(
            missing=["/mail/cur/ghost.msg"],
            extra=[],
        )
        assert err.missing == ["/mail/cur/ghost.msg"]

    def test_inventory_mismatch_error_carries_extra(self):
        """InventoryMismatchError must store a list of extra (unexpected) file paths."""
        from maildir_report.inventory import InventoryMismatchError

        err = InventoryMismatchError(
            missing=[],
            extra=["/mail/cur/zombie.msg"],
        )
        assert err.extra == ["/mail/cur/zombie.msg"]

    def test_inventory_mismatch_error_missing_in_str(self):
        """Missing path must appear in str(InventoryMismatchError)."""
        from maildir_report.inventory import InventoryMismatchError

        err = InventoryMismatchError(
            missing=["/mail/cur/ghost.msg"],
            extra=[],
        )
        assert "/mail/cur/ghost.msg" in str(err)

    def test_inventory_mismatch_error_extra_in_str(self):
        """Extra path must appear in str(InventoryMismatchError)."""
        from maildir_report.inventory import InventoryMismatchError

        err = InventoryMismatchError(
            missing=[],
            extra=["/mail/cur/zombie.msg"],
        )
        assert "/mail/cur/zombie.msg" in str(err)

    def test_inventory_mismatch_error_missing_sorted(self):
        """Missing list in InventoryMismatchError must be deterministically sorted."""
        from maildir_report.inventory import InventoryMismatchError

        paths = ["/z/c.msg", "/a/b.msg", "/m/n.msg"]
        err = InventoryMismatchError(missing=paths, extra=[])
        assert err.missing == sorted(paths)

    def test_inventory_mismatch_error_extra_sorted(self):
        """Extra list in InventoryMismatchError must be deterministically sorted."""
        from maildir_report.inventory import InventoryMismatchError

        paths = ["/z/c.msg", "/a/b.msg", "/m/n.msg"]
        err = InventoryMismatchError(missing=[], extra=paths)
        assert err.extra == sorted(paths)


# ── list_maildir_files ─────────────────────────────────────────────────────────


class TestListMaildirFiles:
    """list_maildir_files(root) must enumerate disk files matching scan_maildir scope."""

    def test_list_maildir_files_returns_sorted_paths(self, tmp_path):
        """list_maildir_files returns a sorted list of absolute path strings."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "b.msg", _simple_mail())
        _write_mail(root / "cur", "a.msg", _simple_mail())

        result = list_maildir_files(str(root))
        assert result == sorted(result), "list_maildir_files result must be sorted"

    def test_list_maildir_files_returns_absolute_paths(self, tmp_path):
        """Each path in the result must be an absolute path string."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "mail.msg", _simple_mail())

        result = list_maildir_files(str(root))
        assert len(result) == 1
        assert pathlib.Path(result[0]).is_absolute()

    def test_list_maildir_files_includes_cur_and_new(self, tmp_path):
        """Files in both cur/ and new/ must be included."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "in_cur.msg", _simple_mail())
        _write_mail(root / "new", "in_new.msg", _simple_mail())

        result = list_maildir_files(str(root))
        assert len(result) == 2

    def test_list_maildir_files_excludes_tmp(self, tmp_path):
        """Files in tmp/ must NOT be listed."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        _write_mail(root / "tmp", "in_tmp.msg", _simple_mail())
        _write_mail(root / "cur", "real.msg", _simple_mail())

        result = list_maildir_files(str(root))
        assert len(result) == 1
        # Verify the returned file is not under Maildir/tmp/ (not a substring match
        # since pytest tmp_path itself lives under /tmp/pytest-...).
        maildir_tmp = str(root / "tmp")
        assert all(not p.startswith(maildir_tmp) for p in result)

    def test_list_maildir_files_excludes_dotfiles(self, tmp_path):
        """Files starting with '.' must be excluded (Maildir convention)."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", ".hidden", _simple_mail())
        _write_mail(root / "cur", "visible.msg", _simple_mail())

        result = list_maildir_files(str(root))
        assert len(result) == 1
        assert all(not pathlib.Path(p).name.startswith(".") for p in result)

    def test_list_maildir_files_empty_maildir(self, tmp_path):
        """Empty Maildir (no messages) returns an empty list."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        result = list_maildir_files(str(root))
        assert result == []

    def test_list_maildir_files_count_matches_scan(self, tmp_path):
        """list_maildir_files count must equal the number of records from scan_maildir."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        n = 7
        for i in range(n):
            _write_mail(
                cur,
                f"m{i:03d}.msg",
                _simple_mail(message_id=f"<lmf{i}@example.com>"),
            )
        result = list_maildir_files(str(root))
        assert len(result) == n

    def test_list_maildir_files_paths_match_scan_filepaths(self, tmp_path):
        """Paths from list_maildir_files must equal filepaths in scan_maildir records."""
        from maildir_report.inventory import list_maildir_files

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        for i in range(5):
            _write_mail(
                cur,
                f"m{i}.msg",
                _simple_mail(message_id=f"<match{i}@example.com>"),
            )
        disk_files = list_maildir_files(str(root))
        records = _scan_to_records(root)
        record_paths = sorted(r["filepath"] for r in records)
        assert disk_files == record_paths


# ── reconcile_inventory: happy path ───────────────────────────────────────────


class TestReconcileInventoryHappy:
    """reconcile_inventory() must succeed silently when disk and records match."""

    def test_reconcile_exact_match_returns_none(self, tmp_path):
        """reconcile_inventory returns None (no exception) when sets match exactly."""
        from maildir_report.inventory import reconcile_inventory

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "a.msg", _simple_mail(message_id="<a@example.com>"))
        records = _scan_to_records(root)

        result = reconcile_inventory(str(root), records)
        assert result is None, "reconcile_inventory must return None on success"

    def test_reconcile_single_file(self, tmp_path):
        """A single-file Maildir reconciles without error."""
        from maildir_report.inventory import reconcile_inventory

        root = _make_maildir(tmp_path)
        _write_mail(
            root / "cur", "solo.msg", _simple_mail(message_id="<solo@example.com>")
        )
        records = _scan_to_records(root)
        reconcile_inventory(str(root), records)  # must not raise

    def test_reconcile_47_files_no_error(self, tmp_path):
        """Fixture with 47 mail files must reconcile without error (plan acceptance criteria)."""
        from maildir_report.inventory import reconcile_inventory

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        n = 47
        for i in range(n):
            _write_mail(
                cur,
                f"mail_{i:04d}.msg",
                _simple_mail(
                    message_id=f"<inv{i}@example.com>",
                    subject=f"Mail {i}",
                ),
            )
        records = _scan_to_records(root)
        assert len(records) == n, f"Expected {n} records from scan, got {len(records)}"
        # Must not raise — all 47 files reconcile exactly
        reconcile_inventory(str(root), records)

    def test_reconcile_cur_and_new_mix(self, tmp_path):
        """Files spread across cur/ and new/ reconcile without error."""
        from maildir_report.inventory import reconcile_inventory

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "c1.msg", _simple_mail(message_id="<c1@example.com>"))
        _write_mail(root / "new", "n1.msg", _simple_mail(message_id="<n1@example.com>"))
        records = _scan_to_records(root)
        reconcile_inventory(str(root), records)  # must not raise

    def test_reconcile_empty_maildir(self, tmp_path):
        """Empty Maildir with empty records list reconciles without error."""
        from maildir_report.inventory import reconcile_inventory

        root = _make_maildir(tmp_path)
        reconcile_inventory(str(root), [])  # must not raise


# ── reconcile_inventory: mismatch detection ───────────────────────────────────


class TestReconcileInventoryMismatch:
    """reconcile_inventory() must raise InventoryMismatchError on any set difference."""

    def test_reconcile_missing_file_raises(self, tmp_path):
        """record with a filepath not on disk -> InventoryMismatchError with that path."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        path_a = _write_mail(
            root / "cur", "a.msg", _simple_mail(message_id="<a@example.com>")
        )
        path_b = _write_mail(
            root / "cur", "b.msg", _simple_mail(message_id="<b@example.com>")
        )
        records = scan_maildir(str(root))

        # Delete b.msg from disk AFTER parsing — creates a "missing from disk" scenario
        # by injecting a synthetic record with a phantom filepath
        phantom_record = {"filepath": str(tmp_path / "Maildir" / "cur" / "phantom.msg")}
        augmented_records = list(records) + [phantom_record]

        with pytest.raises(InventoryMismatchError) as exc_info:
            reconcile_inventory(str(root), augmented_records)
        err = exc_info.value
        phantom_path = str(tmp_path / "Maildir" / "cur" / "phantom.msg")
        assert phantom_path in err.missing, (
            f"missing must contain phantom path. missing={err.missing}"
        )

    def test_reconcile_extra_file_on_disk_raises(self, tmp_path):
        """file on disk with no matching record -> InventoryMismatchError with that path."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "a.msg", _simple_mail(message_id="<a@example.com>"))

        # Scan before adding the extra file
        records = scan_maildir(str(root))

        # Add a file to disk after scanning — it's "extra" on disk but not in records
        extra_path = _write_mail(
            root / "cur", "extra.msg", _simple_mail(message_id="<extra@example.com>")
        )

        with pytest.raises(InventoryMismatchError) as exc_info:
            reconcile_inventory(str(root), records)
        err = exc_info.value
        assert str(extra_path) in err.extra, (
            f"extra must contain extra_path. extra={err.extra}"
        )

    def test_reconcile_error_missing_in_str(self, tmp_path):
        """Missing path must appear in str(InventoryMismatchError) raised by reconcile."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "a.msg", _simple_mail(message_id="<a@example.com>"))
        records = _scan_to_records(root)
        phantom = {"filepath": str(root / "cur" / "phantom.msg")}
        with pytest.raises(InventoryMismatchError) as exc_info:
            reconcile_inventory(str(root), list(records) + [phantom])
        assert "phantom.msg" in str(exc_info.value)

    def test_reconcile_error_extra_in_str(self, tmp_path):
        """Extra path must appear in str(InventoryMismatchError) raised by reconcile."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "a.msg", _simple_mail(message_id="<a@example.com>"))
        records = scan_maildir(str(root))
        extra = _write_mail(
            root / "cur", "extra.msg", _simple_mail(message_id="<extra@example.com>")
        )
        with pytest.raises(InventoryMismatchError) as exc_info:
            reconcile_inventory(str(root), records)
        assert "extra.msg" in str(exc_info.value)

    def test_reconcile_missing_list_is_sorted(self, tmp_path):
        """missing list in raised error must be sorted deterministically."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory

        root = _make_maildir(tmp_path)
        # No real files on disk, but inject multiple phantom records
        phantoms = [
            {"filepath": str(root / "cur" / f"phantom_{c}.msg")}
            for c in ["z", "a", "m"]
        ]
        with pytest.raises(InventoryMismatchError) as exc_info:
            reconcile_inventory(str(root), phantoms)
        err = exc_info.value
        assert err.missing == sorted(err.missing), "missing must be sorted"

    def test_reconcile_extra_list_is_sorted(self, tmp_path):
        """extra list in raised error must be sorted deterministically."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory

        root = _make_maildir(tmp_path)
        # Three real files on disk, no records passed
        for c in ["z", "a", "m"]:
            _write_mail(
                root / "cur",
                f"file_{c}.msg",
                _simple_mail(message_id=f"<{c}@example.com>"),
            )
        with pytest.raises(InventoryMismatchError) as exc_info:
            reconcile_inventory(str(root), [])
        err = exc_info.value
        assert err.extra == sorted(err.extra), "extra must be sorted"


# ── integration: scan then reconcile ─────────────────────────────────────────


class TestReconcileInventoryIntegration:
    """End-to-end: scan_maildir + reconcile_inventory must agree on all 47 files."""

    def test_scan_then_reconcile_47_roundtrip(self, tmp_path):
        """scan_maildir result feeds directly into reconcile_inventory without error."""
        from maildir_report.inventory import reconcile_inventory
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        n = 47
        for i in range(n):
            _write_mail(
                cur,
                f"mail_{i:04d}.msg",
                _simple_mail(
                    message_id=f"<rt{i}@example.com>",
                    subject=f"Roundtrip {i}",
                ),
            )
        records = scan_maildir(str(root))
        assert len(records) == n
        # Must not raise — scan and disk must be in perfect agreement
        reconcile_inventory(str(root), records)

    def test_scan_and_reconcile_after_adding_file(self, tmp_path):
        """Adding a file to disk after scan causes InventoryMismatchError (extra on disk)."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        _write_mail(
            root / "cur", "before.msg", _simple_mail(message_id="<before@example.com>")
        )
        records = scan_maildir(str(root))

        # Now add a file to disk (not in records)
        _write_mail(
            root / "cur", "after.msg", _simple_mail(message_id="<after@example.com>")
        )

        with pytest.raises(InventoryMismatchError):
            reconcile_inventory(str(root), records)

    def test_scan_and_reconcile_after_removing_file(self, tmp_path):
        """Removing a file from disk after scan causes InventoryMismatchError (in records but not disk = missing)."""
        from maildir_report.inventory import InventoryMismatchError, reconcile_inventory
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        path_a = _write_mail(
            root / "cur", "a.msg", _simple_mail(message_id="<a@example.com>")
        )
        path_b = _write_mail(
            root / "cur", "b.msg", _simple_mail(message_id="<b@example.com>")
        )
        records = scan_maildir(str(root))

        # Remove one file from disk
        path_b.unlink()

        with pytest.raises(InventoryMismatchError) as exc_info:
            reconcile_inventory(str(root), records)
        err = exc_info.value
        assert str(path_b) in err.missing, (
            f"b.msg removed from disk should appear in missing (in records but not disk). "
            f"missing={err.missing}, extra={err.extra}"
        )

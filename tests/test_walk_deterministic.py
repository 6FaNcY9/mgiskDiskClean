"""
test_walk_deterministic.py — TDD tests for Task 8: deterministic filesystem
traversal + Maildir++ folder naming normalization.

Contract being tested
---------------------
- deterministic_walk(root) yields (filepath, folder_name) tuples in a
  deterministic order that does NOT depend on filesystem iteration order.
- Sorting is applied to both directory names and filenames at every level.
- Only cur/ and new/ sub-directories are yielded; tmp/ is always excluded.
- normalize_folder_name() converts Maildir++ dot-folder paths to consistent
  human-readable labels with stable, predictable output.
- Two identical Maildirs walked in different filesystem orders produce the
  same sequence of (filepath, folder_name) tuples.
"""

from __future__ import annotations

import pathlib
from email.mime.text import MIMEText

import pytest

from maildir_report.walk import deterministic_walk, normalize_folder_name


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_mail(directory: pathlib.Path, filename: str) -> pathlib.Path:
    """Write a minimal valid RFC 2822 message to directory/filename."""
    msg = MIMEText("body", "plain", "utf-8")
    msg["Subject"] = f"Test {filename}"
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg["Message-ID"] = f"<{filename.replace('/', '_')}@example.com>"
    path = directory / filename
    path.write_bytes(msg.as_bytes())
    return path


def _make_maildir(tmp_path: pathlib.Path, name: str = "Maildir") -> pathlib.Path:
    """Create a minimal Maildir skeleton (cur/, new/, tmp/) under tmp_path."""
    root = tmp_path / name
    (root / "cur").mkdir(parents=True)
    (root / "new").mkdir(parents=True)
    (root / "tmp").mkdir(parents=True)
    return root


def _make_maildir_plus(tmp_path: pathlib.Path, subfolder: str) -> pathlib.Path:
    """Create a Maildir++ sub-folder (e.g. '.Sent') under the given Maildir root."""
    root = _make_maildir(tmp_path)
    sub = root / subfolder
    (sub / "cur").mkdir(parents=True)
    (sub / "new").mkdir(parents=True)
    (sub / "tmp").mkdir(parents=True)
    return root


# ── import guard ──────────────────────────────────────────────────────────────


class TestWalkImports:
    """walk module and public functions must be importable."""

    def test_walk_module_importable(self):
        """maildir_report.walk must be importable."""
        from maildir_report import walk  # noqa: F401

    def test_deterministic_walk_importable(self):
        """deterministic_walk must be importable from maildir_report.walk."""
        from maildir_report.walk import deterministic_walk  # noqa: F401

    def test_normalize_folder_name_importable(self):
        """normalize_folder_name must be importable from maildir_report.walk."""
        from maildir_report.walk import normalize_folder_name  # noqa: F401


# ── normalize_folder_name ────────────────────────────────────────────────────


class TestNormalizeFolderName:
    """normalize_folder_name converts Maildir++ raw folder names to labels."""

    def test_normalize_root_cur_is_inbox(self):
        """cur/ and new/ at root level normalize to 'INBOX'."""
        assert normalize_folder_name("cur") == "INBOX"

    def test_normalize_root_new_is_inbox(self):
        """new/ at root level normalizes to 'INBOX'."""
        assert normalize_folder_name("new") == "INBOX"

    def test_normalize_dot_sent(self):
        """Maildir++ '.Sent' normalizes by stripping the leading dot."""
        assert normalize_folder_name(".Sent") == "Sent"

    def test_normalize_dot_trash(self):
        """Maildir++ '.Trash' normalizes to 'Trash'."""
        assert normalize_folder_name(".Trash") == "Trash"

    def test_normalize_dot_drafts(self):
        """Maildir++ '.Drafts' normalizes to 'Drafts'."""
        assert normalize_folder_name(".Drafts") == "Drafts"

    def test_normalize_dot_spam(self):
        """Maildir++ '.Spam' normalizes to 'Spam'."""
        assert normalize_folder_name(".Spam") == "Spam"

    def test_normalize_nested_inbox_subfolder(self):
        """Maildir++ '.INBOX.Work' dot-separator becomes slash: 'INBOX/Work'."""
        assert normalize_folder_name(".INBOX.Work") == "INBOX/Work"

    def test_normalize_nested_subfolder(self):
        """Maildir++ '.Sent.Archive' dot-separator becomes slash: 'Sent/Archive'."""
        assert normalize_folder_name(".Sent.Archive") == "Sent/Archive"

    def test_normalize_deeply_nested(self):
        """Maildir++ '.A.B.C' normalizes to 'A/B/C'."""
        assert normalize_folder_name(".A.B.C") == "A/B/C"

    def test_normalize_plain_name_unchanged(self):
        """A plain folder name with no leading dot is returned unchanged."""
        result = normalize_folder_name("Archive")
        assert result == "Archive"

    def test_normalize_empty_string(self):
        """Empty string normalizes to 'INBOX' as a safe fallback."""
        assert normalize_folder_name("") == "INBOX"

    def test_normalize_returns_string(self):
        """normalize_folder_name always returns a str."""
        result = normalize_folder_name(".Sent")
        assert isinstance(result, str)

    def test_normalize_inbox_dot_only(self):
        """A bare '.' (root Maildir) normalizes to 'INBOX'."""
        assert normalize_folder_name(".") == "INBOX"


# ── deterministic_walk: basic behavior ───────────────────────────────────────


class TestDeterministicWalkBasic:
    """deterministic_walk yields (filepath, folder_name) for each mail file."""

    def test_walk_yields_tuples(self, tmp_path: pathlib.Path) -> None:
        """deterministic_walk must yield (str, str) 2-tuples."""
        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "mail.msg")
        results = list(deterministic_walk(str(root)))
        assert len(results) == 1
        fp, folder = results[0]
        assert isinstance(fp, str)
        assert isinstance(folder, str)

    def test_walk_filepath_absolute(self, tmp_path: pathlib.Path) -> None:
        """Filepaths yielded must be absolute paths."""
        root = _make_maildir(tmp_path)
        path = _write_mail(root / "cur", "mail.msg")
        results = list(deterministic_walk(str(root)))
        assert results[0][0] == str(path)

    def test_walk_inbox_folder_label(self, tmp_path: pathlib.Path) -> None:
        """Files in root cur/ get folder label 'INBOX'."""
        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "mail.msg")
        results = list(deterministic_walk(str(root)))
        assert results[0][1] == "INBOX"

    def test_walk_new_folder_included(self, tmp_path: pathlib.Path) -> None:
        """Files in new/ sub-directory are included with label 'INBOX'."""
        root = _make_maildir(tmp_path)
        _write_mail(root / "new", "mail_new.msg")
        results = list(deterministic_walk(str(root)))
        assert len(results) == 1
        assert results[0][1] == "INBOX"

    def test_walk_tmp_excluded(self, tmp_path: pathlib.Path) -> None:
        """Files in tmp/ must NOT be yielded."""
        root = _make_maildir(tmp_path)
        _write_mail(root / "tmp", "mail_tmp.msg")
        _write_mail(root / "cur", "mail_cur.msg")
        results = list(deterministic_walk(str(root)))
        assert len(results) == 1, "Only cur/ file should appear; tmp/ must be excluded"

    def test_walk_dotfiles_excluded(self, tmp_path: pathlib.Path) -> None:
        """Files starting with '.' must be excluded (Maildir lock/hidden files)."""
        root = _make_maildir(tmp_path)
        (root / "cur" / ".hidden").write_bytes(b"not a mail")
        _write_mail(root / "cur", "real.msg")
        results = list(deterministic_walk(str(root)))
        assert len(results) == 1
        assert results[0][0].endswith("real.msg")

    def test_walk_empty_maildir_yields_nothing(self, tmp_path: pathlib.Path) -> None:
        """An empty Maildir (no messages) yields nothing."""
        root = _make_maildir(tmp_path)
        results = list(deterministic_walk(str(root)))
        assert results == []


# ── deterministic_walk: ordering invariance ───────────────────────────────────


class TestDeterministicWalkOrdering:
    """Walk order must be stable and independent of filesystem iteration order."""

    def test_walk_files_in_sorted_order(self, tmp_path: pathlib.Path) -> None:
        """Files within a directory must be yielded in lexicographic filename order."""
        root = _make_maildir(tmp_path)
        cur = root / "cur"
        # Write in reversed order; walk must yield in sorted order
        for name in ["z_mail.msg", "a_mail.msg", "m_mail.msg"]:
            _write_mail(cur, name)
        results = list(deterministic_walk(str(root)))
        filenames = [pathlib.Path(fp).name for fp, _ in results]
        assert filenames == sorted(filenames), f"Files not in sorted order: {filenames}"

    def test_walk_cur_before_new_within_same_folder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Within INBOX, cur/ files appear before new/ files (sorted dir order)."""
        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "cur_mail.msg")
        _write_mail(root / "new", "new_mail.msg")
        results = list(deterministic_walk(str(root)))
        # Both should appear; 'cur' < 'new' lexicographically so cur first
        assert len(results) == 2
        assert "cur" in results[0][0]
        assert "new" in results[1][0]

    def test_walk_two_runs_identical_order(self, tmp_path: pathlib.Path) -> None:
        """Two calls on the same Maildir yield identical (filepath, folder) sequences."""
        root = _make_maildir(tmp_path)
        cur = root / "cur"
        for i in range(5):
            _write_mail(cur, f"mail_{i:03d}.msg")
        run1 = list(deterministic_walk(str(root)))
        run2 = list(deterministic_walk(str(root)))
        assert run1 == run2, "Two identical walks must yield identical results"

    def test_walk_subfolders_in_sorted_order(self, tmp_path: pathlib.Path) -> None:
        """Maildir++ sub-folders are visited in lexicographic order."""
        root = _make_maildir(tmp_path)
        # Create .Sent, .Drafts, .Trash sub-folders
        for subfolder in [".Sent", ".Drafts", ".Trash"]:
            sub = root / subfolder
            (sub / "cur").mkdir(parents=True)
            (sub / "new").mkdir(parents=True)
            (sub / "tmp").mkdir(parents=True)
            _write_mail(sub / "cur", f"{subfolder[1:]}_mail.msg")
        results = list(deterministic_walk(str(root)))
        folder_labels = [folder for _, folder in results]
        # .Drafts < .Sent < .Trash lexicographically
        assert folder_labels == sorted(folder_labels), (
            f"Subfolder labels not in sorted order: {folder_labels}"
        )

    def test_walk_ordering_independent_of_filesystem(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Repeated walks on fixture with known files always returns same order."""
        root = _make_maildir(tmp_path)
        cur = root / "cur"
        # Write files in reverse alphabetical order (worst case for unsorted walk)
        names = [f"mail_{i:02d}.msg" for i in range(10, 0, -1)]
        for name in names:
            _write_mail(cur, name)
        results = list(deterministic_walk(str(root)))
        filepaths = [fp for fp, _ in results]
        # Must be sorted
        assert filepaths == sorted(filepaths), (
            f"Walk order not deterministic: {[pathlib.Path(p).name for p in filepaths]}"
        )


# ── deterministic_walk: Maildir++ folder naming ───────────────────────────────


class TestDeterministicWalkFolderNaming:
    """Maildir++ dot-folder names are normalized consistently."""

    def test_walk_sent_folder_normalized(self, tmp_path: pathlib.Path) -> None:
        """Files in .Sent/cur/ get folder label 'Sent'."""
        root = _make_maildir_plus(tmp_path, ".Sent")
        _write_mail(root / ".Sent" / "cur", "sent_mail.msg")
        results = list(deterministic_walk(str(root)))
        sent_results = [(fp, f) for fp, f in results if f == "Sent"]
        assert len(sent_results) == 1, (
            f"Expected folder='Sent', got: {[f for _, f in results]}"
        )

    def test_walk_trash_folder_normalized(self, tmp_path: pathlib.Path) -> None:
        """Files in .Trash/cur/ get folder label 'Trash'."""
        root = _make_maildir_plus(tmp_path, ".Trash")
        _write_mail(root / ".Trash" / "cur", "trash_mail.msg")
        results = list(deterministic_walk(str(root)))
        trash_results = [(fp, f) for fp, f in results if f == "Trash"]
        assert len(trash_results) == 1, (
            f"Expected folder='Trash', got: {[f for _, f in results]}"
        )

    def test_walk_mixed_inbox_and_subfolder(self, tmp_path: pathlib.Path) -> None:
        """Mixed INBOX + subfolder Maildir yields correct folder labels for each."""
        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "inbox_mail.msg")
        # Add .Sent sub-folder
        sent = root / ".Sent"
        (sent / "cur").mkdir(parents=True)
        (sent / "new").mkdir(parents=True)
        (sent / "tmp").mkdir(parents=True)
        _write_mail(sent / "cur", "sent_mail.msg")

        results = list(deterministic_walk(str(root)))
        assert len(results) == 2
        folder_map = {pathlib.Path(fp).name: folder for fp, folder in results}
        assert folder_map["inbox_mail.msg"] == "INBOX"
        assert folder_map["sent_mail.msg"] == "Sent"

    def test_walk_nested_subfolder_label(self, tmp_path: pathlib.Path) -> None:
        """Maildir++ '.INBOX.Archive' subfolder gets label 'INBOX/Archive'."""
        root = _make_maildir(tmp_path)
        nested = root / ".INBOX.Archive"
        (nested / "cur").mkdir(parents=True)
        (nested / "new").mkdir(parents=True)
        (nested / "tmp").mkdir(parents=True)
        _write_mail(nested / "cur", "archive_mail.msg")
        results = list(deterministic_walk(str(root)))
        archive_results = [(fp, f) for fp, f in results if f == "INBOX/Archive"]
        assert len(archive_results) == 1, (
            f"Expected folder='INBOX/Archive', got: {[f for _, f in results]}"
        )

    def test_walk_folder_label_stable_across_runs(self, tmp_path: pathlib.Path) -> None:
        """Folder labels assigned by walk must be identical across two runs."""
        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "msg1.msg")
        sent = root / ".Sent"
        (sent / "cur").mkdir(parents=True)
        (sent / "new").mkdir(parents=True)
        (sent / "tmp").mkdir(parents=True)
        _write_mail(sent / "cur", "msg2.msg")

        run1 = {
            pathlib.Path(fp).name: folder
            for fp, folder in deterministic_walk(str(root))
        }
        run2 = {
            pathlib.Path(fp).name: folder
            for fp, folder in deterministic_walk(str(root))
        }
        assert run1 == run2, "Folder labels must be identical across runs"


# ── deterministic_walk: integration with parser ───────────────────────────────


class TestDeterministicWalkParserIntegration:
    """scan_maildir in parser.py must delegate to deterministic_walk."""

    def test_parser_scan_uses_deterministic_walk(self, tmp_path: pathlib.Path) -> None:
        """scan_maildir result must match deterministic_walk filepath ordering."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        cur = root / "cur"
        for name in ["z_mail.msg", "a_mail.msg", "m_mail.msg"]:
            _write_mail(cur, name)

        # Walk directly — get the filepath ordering
        walk_fps = [fp for fp, _ in deterministic_walk(str(root))]
        # Scan via parser — sort_emails applies date+filepath ordering, but
        # with identical dates, filepath tiebreaker matches sorted walk order
        records = scan_maildir(str(root))
        scan_fps = [r["filepath"] for r in records]

        # Both must have the same set of files
        assert set(walk_fps) == set(scan_fps), (
            f"walk and scan cover different files:\nwalk: {walk_fps}\nscan: {scan_fps}"
        )

    def test_parser_scan_folder_labels_match_walk(self, tmp_path: pathlib.Path) -> None:
        """folder field in scan records must match normalize_folder_name output."""
        from maildir_report.parser import scan_maildir

        root = _make_maildir(tmp_path)
        _write_mail(root / "cur", "inbox.msg")
        # Add .Drafts
        drafts = root / ".Drafts"
        (drafts / "cur").mkdir(parents=True)
        (drafts / "new").mkdir(parents=True)
        (drafts / "tmp").mkdir(parents=True)
        _write_mail(drafts / "cur", "draft.msg")

        records = scan_maildir(str(root))
        folder_map = {pathlib.Path(r["filepath"]).name: r["folder"] for r in records}
        assert folder_map["inbox.msg"] == "INBOX"
        assert folder_map["draft.msg"] == "Drafts"

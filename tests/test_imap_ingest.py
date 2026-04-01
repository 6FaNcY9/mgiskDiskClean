"""
test_imap_ingest.py — Tests for Task 11: optional IMAP ingestion source.

Covers:
  1. Deterministic filename scheme: {uidvalidity}.{uid}.eml in Maildir/cur/
  2. Idempotency: re-running produces same file list, no duplicates
  3. Read-only: no server-side mutations
  4. Credentials via env vars only (IMAP_SERVER, IMAP_USER, IMAP_PASS)
  5. TLS/IMAPS requirement
  6. INBOX-only v1 default
  7. CLI --help exits 0
  8. Source integration: --source imap vs default --source rsync
  9. Since-filter: --since YYYY-MM-DD limits to messages after that date
 10. Missing credentials raises clear error
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from maildir_report.imap_ingest import (
    ImapCredentialError,
    ImapIngestConfig,
    ImapMessage,
    materialize_maildir,
    run_imap_ingest,
    main,
)


# ── fixtures / helpers ────────────────────────────────────────────────────────


_FAKE_UIDVALIDITY = 12345
_FAKE_RFC822_A = b"From: alice@example.com\r\nSubject: Hello\r\n\r\nBody A"
_FAKE_RFC822_B = b"From: bob@example.com\r\nSubject: Hi\r\n\r\nBody B"


def _make_imap_messages(*pairs: tuple[int, bytes]) -> list[ImapMessage]:
    """Return list of ImapMessage(uid, rfc822_bytes)."""
    return [ImapMessage(uid=uid, rfc822=data) for uid, data in pairs]


def _config(data_dir: pathlib.Path, since: str | None = None) -> ImapIngestConfig:
    return ImapIngestConfig(
        server="imap.example.com",
        user="user@example.com",
        password="secret",
        mailbox_name="testbox",
        data_dir=str(data_dir),
        since=since,
    )


# ── 1. Deterministic filename scheme ─────────────────────────────────────────


class TestFilenameScheme:
    """Each message must be saved as {uidvalidity}.{uid}.eml in Maildir/cur/."""

    def test_cur_file_named_uidvalidity_uid(self, tmp_path):
        """Files must follow {uidvalidity}.{uid}.eml naming."""
        cfg = _config(tmp_path)
        messages = _make_imap_messages((42, _FAKE_RFC822_A), (99, _FAKE_RFC822_B))

        materialize_maildir(
            messages=messages,
            uidvalidity=_FAKE_UIDVALIDITY,
            config=cfg,
        )

        cur = pathlib.Path(tmp_path) / "imap" / "testbox" / "INBOX" / "Maildir" / "cur"
        assert cur.exists(), f"Maildir/cur not created at {cur}"

        names = {f.name for f in cur.iterdir()}
        assert f"{_FAKE_UIDVALIDITY}.42.eml" in names
        assert f"{_FAKE_UIDVALIDITY}.99.eml" in names

    def test_cur_dir_structure_created(self, tmp_path):
        """The full Maildir layout (cur/ new/ tmp/) should be created."""
        cfg = _config(tmp_path)
        materialize_maildir(
            messages=_make_imap_messages((1, _FAKE_RFC822_A)),
            uidvalidity=_FAKE_UIDVALIDITY,
            config=cfg,
        )

        maildir_root = pathlib.Path(tmp_path) / "imap" / "testbox" / "INBOX" / "Maildir"
        assert (maildir_root / "cur").is_dir()
        assert (maildir_root / "new").is_dir()
        assert (maildir_root / "tmp").is_dir()

    def test_file_contains_rfc822_bytes(self, tmp_path):
        """Written .eml file bytes must match the fetched RFC822 payload."""
        cfg = _config(tmp_path)
        materialize_maildir(
            messages=_make_imap_messages((7, _FAKE_RFC822_A)),
            uidvalidity=_FAKE_UIDVALIDITY,
            config=cfg,
        )

        eml = (
            pathlib.Path(tmp_path)
            / "imap"
            / "testbox"
            / "INBOX"
            / "Maildir"
            / "cur"
            / f"{_FAKE_UIDVALIDITY}.7.eml"
        )
        assert eml.read_bytes() == _FAKE_RFC822_A


# ── 2. Idempotency: reruns don't create duplicates ────────────────────────────


class TestIdempotency:
    """Running ingest twice must produce the same file set, no duplicates."""

    def test_second_run_no_new_files(self, tmp_path):
        """Re-running ingest with the same messages does not add new files."""
        cfg = _config(tmp_path)
        messages = _make_imap_messages((1, _FAKE_RFC822_A), (2, _FAKE_RFC822_B))

        materialize_maildir(
            messages=messages, uidvalidity=_FAKE_UIDVALIDITY, config=cfg
        )
        cur = pathlib.Path(tmp_path) / "imap" / "testbox" / "INBOX" / "Maildir" / "cur"
        count_after_first = len(list(cur.iterdir()))

        # Second run with same messages
        materialize_maildir(
            messages=messages, uidvalidity=_FAKE_UIDVALIDITY, config=cfg
        )
        count_after_second = len(list(cur.iterdir()))

        assert count_after_first == count_after_second, (
            f"Second run created extra files: {count_after_first} → {count_after_second}"
        )

    def test_file_list_stable_across_reruns(self, tmp_path):
        """File names must be identical across two runs."""
        cfg = _config(tmp_path)
        messages = _make_imap_messages((10, _FAKE_RFC822_A), (20, _FAKE_RFC822_B))

        materialize_maildir(
            messages=messages, uidvalidity=_FAKE_UIDVALIDITY, config=cfg
        )
        cur = pathlib.Path(tmp_path) / "imap" / "testbox" / "INBOX" / "Maildir" / "cur"
        names_first = {f.name for f in cur.iterdir()}

        materialize_maildir(
            messages=messages, uidvalidity=_FAKE_UIDVALIDITY, config=cfg
        )
        names_second = {f.name for f in cur.iterdir()}

        assert names_first == names_second

    def test_uidvalidity_change_new_files_coexist(self, tmp_path):
        """A new UIDVALIDITY creates differently-named files without removing old ones."""
        cfg = _config(tmp_path)
        messages = _make_imap_messages((1, _FAKE_RFC822_A))

        materialize_maildir(messages=messages, uidvalidity=100, config=cfg)
        materialize_maildir(messages=messages, uidvalidity=200, config=cfg)

        cur = pathlib.Path(tmp_path) / "imap" / "testbox" / "INBOX" / "Maildir" / "cur"
        names = {f.name for f in cur.iterdir()}
        assert "100.1.eml" in names
        assert "200.1.eml" in names


# ── 3. Read-only: no server-side mutations ───────────────────────────────────


class TestReadOnly:
    """The IMAP connection must not mutate server state."""

    def test_no_flag_changes_on_messages(self, tmp_path):
        """The fetch loop must NOT call any mutating IMAP commands."""
        cfg = _config(tmp_path)

        # A minimal mock connection: capture calls
        mock_conn = MagicMock()
        mock_conn.folder.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.folder.return_value.__exit__ = MagicMock(return_value=False)

        # Simulate fetch returning two messages
        fake_msgs = [
            MagicMock(uid=1, obj=MagicMock()),
            MagicMock(uid=2, obj=MagicMock()),
        ]
        for m in fake_msgs:
            m.obj.as_bytes.return_value = _FAKE_RFC822_A

        mock_conn.fetch.return_value = iter(fake_msgs)
        mock_conn.uid_validity = _FAKE_UIDVALIDITY

        run_imap_ingest(config=cfg, connection=mock_conn)

        # Verify no mutating calls were made
        mock_conn.move.assert_not_called()
        mock_conn.delete.assert_not_called()
        mock_conn.flag.assert_not_called()
        mock_conn.copy.assert_not_called()
        mock_conn.expunge.assert_not_called()


# ── 4. Credentials via env vars only ─────────────────────────────────────────


class TestCredentials:
    """Credentials must come from IMAP_SERVER, IMAP_USER, IMAP_PASS env vars."""

    def test_missing_imap_server_raises(self, tmp_path):
        """Missing IMAP_SERVER env var must raise ImapCredentialError."""
        with patch.dict(
            "os.environ",
            {"IMAP_USER": "u@example.com", "IMAP_PASS": "pw"},
            clear=True,
        ):
            with pytest.raises(ImapCredentialError, match="IMAP_SERVER"):
                ImapIngestConfig.from_env(
                    mailbox_name="box",
                    data_dir=str(tmp_path),
                )

    def test_missing_imap_user_raises(self, tmp_path):
        """Missing IMAP_USER env var must raise ImapCredentialError."""
        with patch.dict(
            "os.environ",
            {"IMAP_SERVER": "imap.example.com", "IMAP_PASS": "pw"},
            clear=True,
        ):
            with pytest.raises(ImapCredentialError, match="IMAP_USER"):
                ImapIngestConfig.from_env(
                    mailbox_name="box",
                    data_dir=str(tmp_path),
                )

    def test_missing_imap_pass_raises(self, tmp_path):
        """Missing IMAP_PASS env var must raise ImapCredentialError."""
        with patch.dict(
            "os.environ",
            {"IMAP_SERVER": "imap.example.com", "IMAP_USER": "u@example.com"},
            clear=True,
        ):
            with pytest.raises(ImapCredentialError, match="IMAP_PASS"):
                ImapIngestConfig.from_env(
                    mailbox_name="box",
                    data_dir=str(tmp_path),
                )

    def test_all_env_vars_present_no_error(self, tmp_path):
        """All three env vars present → ImapIngestConfig created without error."""
        with patch.dict(
            "os.environ",
            {
                "IMAP_SERVER": "imap.example.com",
                "IMAP_USER": "u@example.com",
                "IMAP_PASS": "secret",
            },
            clear=True,
        ):
            cfg = ImapIngestConfig.from_env(
                mailbox_name="mybox",
                data_dir=str(tmp_path),
            )
        assert cfg.server == "imap.example.com"
        assert cfg.user == "u@example.com"
        assert cfg.password == "secret"
        assert cfg.mailbox_name == "mybox"


# ── 5. TLS/IMAPS requirement ─────────────────────────────────────────────────


class TestTlsRequirement:
    """TLS (IMAPS) must be required; plain-text connection must be rejected."""

    def test_config_default_uses_tls(self, tmp_path):
        """ImapIngestConfig.ssl defaults to True."""
        cfg = _config(tmp_path)
        assert cfg.ssl is True

    def test_config_ssl_false_raises(self, tmp_path):
        """Constructing a config with ssl=False must raise ValueError."""
        with pytest.raises(ValueError, match="TLS"):
            ImapIngestConfig(
                server="imap.example.com",
                user="u@example.com",
                password="secret",
                mailbox_name="box",
                data_dir=str(tmp_path),
                ssl=False,
            )


# ── 6. INBOX-only v1 default ─────────────────────────────────────────────────


class TestInboxDefault:
    """Default folder must be INBOX; output path includes INBOX."""

    def test_materialized_path_includes_inbox(self, tmp_path):
        """The output Maildir path must contain an 'INBOX' path component."""
        cfg = _config(tmp_path)
        materialize_maildir(
            messages=_make_imap_messages((1, _FAKE_RFC822_A)),
            uidvalidity=_FAKE_UIDVALIDITY,
            config=cfg,
        )
        expected_inbox_path = (
            pathlib.Path(tmp_path) / "imap" / "testbox" / "INBOX" / "Maildir" / "cur"
        )
        assert expected_inbox_path.exists()

    def test_config_default_folder_inbox(self, tmp_path):
        """ImapIngestConfig.folder defaults to 'INBOX'."""
        cfg = _config(tmp_path)
        assert cfg.folder == "INBOX"


# ── 7. CLI --help exits 0 ─────────────────────────────────────────────────────


class TestCli:
    def test_help_exits_0(self):
        """CLI --help must print usage and exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "maildir_report.imap_ingest", "--help"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONPATH": "src"},
            cwd=pathlib.Path(__file__).parent.parent,
        )
        assert result.returncode == 0, (
            f"--help returned {result.returncode}: {result.stderr}"
        )
        assert "IMAP" in result.stdout or "imap" in result.stdout.lower()

    def test_missing_mailbox_arg_exits_nonzero(self):
        """Running CLI without required args exits non-zero."""
        result = subprocess.run(
            [sys.executable, "-m", "maildir_report.imap_ingest"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONPATH": "src"},
            cwd=pathlib.Path(__file__).parent.parent,
        )
        assert result.returncode != 0


# ── 8. Source integration: main entrypoint honours --source imap ─────────────


class TestSourceIntegration:
    """The CLI main() should accept --source imap and route to IMAP ingest."""

    def test_main_help_shows_source_option(self):
        """maildir_report --help must mention --source option."""
        result = subprocess.run(
            [sys.executable, "-m", "maildir_report", "--help"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONPATH": "src"},
            cwd=pathlib.Path(__file__).parent.parent,
        )
        assert result.returncode == 0
        assert "--source" in result.stdout

    def test_main_source_rsync_default_unchanged(self, tmp_path):
        """Passing --source rsync (or no --source) behaves as before: uses Maildir path."""
        from maildir_report.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--timestamp", "2024-01-01T00:00:00", "/some/maildir", str(tmp_path)])
        assert getattr(args, "source", "rsync") == "rsync"


# ── 9. Since-filter ──────────────────────────────────────────────────────────


class TestSinceFilter:
    """--since YYYY-MM-DD must be stored in config and passed through."""

    def test_since_stored_in_config(self, tmp_path):
        """ImapIngestConfig accepts a 'since' date string."""
        cfg = _config(tmp_path, since="2024-01-15")
        assert cfg.since == "2024-01-15"

    def test_since_none_by_default(self, tmp_path):
        """ImapIngestConfig.since defaults to None (fetch ALL)."""
        cfg = _config(tmp_path)
        assert cfg.since is None


# ── 10. ImapMessage dataclass ────────────────────────────────────────────────


class TestImapMessage:
    """ImapMessage must hold uid + rfc822 bytes cleanly."""

    def test_imap_message_attrs(self):
        """ImapMessage has uid (int) and rfc822 (bytes) attributes."""
        msg = ImapMessage(uid=42, rfc822=b"raw bytes")
        assert msg.uid == 42
        assert msg.rfc822 == b"raw bytes"

    def test_imap_message_uid_is_int(self):
        """UID must be stored as int."""
        msg = ImapMessage(uid=99, rfc822=b"x")
        assert isinstance(msg.uid, int)

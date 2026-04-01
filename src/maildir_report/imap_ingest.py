"""
imap_ingest.py — Optional IMAP ingestion source for maildir_report.

Purpose
-------
Download messages from an IMAP server (IMAPS/port 993 only) and materialise
them as a local Maildir so the existing pipeline can run unchanged.

This is an OPTIONAL alternative to the rsync-based acquisition in
``store-mailbox``.  The default source remains ``source=rsync``.

Design rules
------------
- READ-ONLY: no server-side mutations (no MOVE, DELETE, FLAG, EXPUNGE).
- TLS (IMAPS) is required; plain-text connections are rejected at config time.
- Credentials come from env vars ONLY: ``IMAP_SERVER``, ``IMAP_USER``,
  ``IMAP_PASS``.  Password via CLI args is forbidden.
- Filename determinism: each message is saved as
  ``<data_dir>/imap/<mailbox>/INBOX/Maildir/cur/{uidvalidity}.{uid}.eml``.
  Re-running overwrites the same file (idempotent) and never creates duplicates.
- INBOX-only for v1; folder is configurable but defaults to ``"INBOX"``.
- Optional ``--since YYYY-MM-DD`` to limit fetch to messages after a date.

Public API
----------
ImapMessage(uid, rfc822)
    Lightweight dataclass for a fetched message.

ImapIngestConfig(server, user, password, mailbox_name, data_dir, ...)
    Configuration; validates TLS requirement at construction time.
    ``ImapIngestConfig.from_env(mailbox_name, data_dir)`` reads credentials
    from env vars and raises ``ImapCredentialError`` on any missing var.

materialize_maildir(messages, uidvalidity, config) -> pathlib.Path
    Write a list of ``ImapMessage`` objects into the Maildir ``cur/`` directory
    under ``data_dir/imap/<mailbox>/INBOX/Maildir/``.
    Returns the Maildir root path.  Idempotent.

run_imap_ingest(config, connection=None) -> pathlib.Path
    Fetch messages from IMAP and call ``materialize_maildir``.
    ``connection`` is an optional pre-built connection object injected by tests
    (avoids live network calls in the test suite).
    If ``connection`` is None, the real ``imap-tools`` MailBox is used.
    Returns the Maildir root path.

main(argv=None) -> int
    CLI entrypoint.  ``python -m maildir_report.imap_ingest --help``.

Exit codes
----------
0  — success (Maildir written or up-to-date)
1  — any error (credential missing, connection failure, I/O error)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any


# ── exceptions ────────────────────────────────────────────────────────────────


class ImapCredentialError(Exception):
    """Raised when a required IMAP credential env var is missing or empty."""


# ── data model ────────────────────────────────────────────────────────────────


@dataclass
class ImapMessage:
    """A single message fetched from an IMAP server.

    Parameters
    ----------
    uid:
        IMAP UID (integer) assigned by the server for this folder.
    rfc822:
        Raw RFC-2822 message bytes (the full email, headers + body).
    """

    uid: int
    rfc822: bytes


@dataclass
class ImapIngestConfig:
    """Configuration for one IMAP ingest run.

    Parameters
    ----------
    server:
        IMAP server hostname or IP (e.g. ``"imap.gmail.com"``).
    user:
        IMAP username / login.
    password:
        IMAP password or app password.  Must NOT come from CLI args.
    mailbox_name:
        Logical mailbox label used as the output directory name under
        ``data_dir/imap/<mailbox_name>/``.
    data_dir:
        Root directory for all ingest outputs.  The Maildir is written at
        ``<data_dir>/imap/<mailbox_name>/INBOX/Maildir/``.
    folder:
        IMAP folder to fetch from (default ``"INBOX"``).
    ssl:
        If True (default), use IMAPS (TLS, port 993).  Raises ``ValueError``
        if set to False — plain-text connections are not allowed.
    port:
        IMAP port.  Defaults to 993 (IMAPS).
    since:
        Optional ISO date string ``"YYYY-MM-DD"`` to fetch only messages
        received on or after this date.  ``None`` fetches ALL messages.
    """

    server: str
    user: str
    password: str
    mailbox_name: str
    data_dir: str
    folder: str = "INBOX"
    ssl: bool = True
    port: int = 993
    since: str | None = None

    def __post_init__(self) -> None:
        if not self.ssl:
            raise ValueError(
                "TLS is required for IMAP connections. "
                "Plain-text (ssl=False) connections are not allowed. "
                "Use IMAPS (port 993) with ssl=True."
            )

    @classmethod
    def from_env(
        cls,
        mailbox_name: str,
        data_dir: str,
        folder: str = "INBOX",
        since: str | None = None,
    ) -> "ImapIngestConfig":
        """Build config from environment variables.

        Reads ``IMAP_SERVER``, ``IMAP_USER``, ``IMAP_PASS``.
        Raises ``ImapCredentialError`` if any variable is missing or empty.

        Parameters
        ----------
        mailbox_name:
            Mailbox label for output directory naming.
        data_dir:
            Root data directory.
        folder:
            IMAP folder (default ``"INBOX"``).
        since:
            Optional date filter (``"YYYY-MM-DD"``).
        """
        server = os.environ.get("IMAP_SERVER", "")
        user = os.environ.get("IMAP_USER", "")
        password = os.environ.get("IMAP_PASS", "")

        if not server:
            raise ImapCredentialError(
                "Required environment variable IMAP_SERVER is missing or empty. "
                "Set it to your IMAP server hostname."
            )
        if not user:
            raise ImapCredentialError(
                "Required environment variable IMAP_USER is missing or empty. "
                "Set it to your IMAP login username."
            )
        if not password:
            raise ImapCredentialError(
                "Required environment variable IMAP_PASS is missing or empty. "
                "Set it to your IMAP password or app password."
            )

        return cls(
            server=server,
            user=user,
            password=password,
            mailbox_name=mailbox_name,
            data_dir=data_dir,
            folder=folder,
            since=since,
        )


# ── core I/O ──────────────────────────────────────────────────────────────────


def _maildir_root(config: ImapIngestConfig) -> pathlib.Path:
    """Return the Maildir root path for the given config.

    Layout: ``<data_dir>/imap/<mailbox_name>/<folder>/Maildir/``
    """
    return (
        pathlib.Path(config.data_dir)
        / "imap"
        / config.mailbox_name
        / config.folder
        / "Maildir"
    )


def materialize_maildir(
    messages: list[ImapMessage],
    uidvalidity: int,
    config: ImapIngestConfig,
) -> pathlib.Path:
    """Write fetched IMAP messages into a local Maildir ``cur/`` directory.

    Each message is saved as::

        <data_dir>/imap/<mailbox>/INBOX/Maildir/cur/{uidvalidity}.{uid}.eml

    Idempotent: if the file already exists with the same content, it is
    overwritten (same name = same bytes guaranteed by IMAP UIDVALIDITY + UID).

    Parameters
    ----------
    messages:
        List of ``ImapMessage`` objects to write.
    uidvalidity:
        The folder's UIDVALIDITY value.  Incorporated into every filename to
        prevent UID collisions across UIDVALIDITY epochs.
    config:
        Ingest configuration (determines output path).

    Returns
    -------
    pathlib.Path
        The Maildir root (parent of ``cur/``, ``new/``, ``tmp/``).
    """
    root = _maildir_root(config)

    # Create standard Maildir layout
    for subdir in ("cur", "new", "tmp"):
        (root / subdir).mkdir(parents=True, exist_ok=True)

    cur = root / "cur"

    for msg in messages:
        filename = f"{uidvalidity}.{msg.uid}.eml"
        dest = cur / filename
        # Overwrite (idempotent): same uidvalidity+uid always produces same name
        dest.write_bytes(msg.rfc822)

    return root


def run_imap_ingest(
    config: ImapIngestConfig,
    connection: Any = None,
) -> pathlib.Path:
    """Fetch messages from IMAP and materialise them as a local Maildir.

    This is the high-level orchestration function.  It:

    1. Opens an IMAPS connection (or uses the injected ``connection``).
    2. Selects the configured folder (default INBOX).
    3. Fetches RFC-2822 bytes for all messages (``ALL`` criteria, or date-limited
       when ``config.since`` is set).
    4. Calls ``materialize_maildir()`` to write files deterministically.
    5. Returns the Maildir root path.

    **Read-only guarantee**: this function never calls any IMAP command that
    mutates server state (no STORE/COPY/MOVE/EXPUNGE).

    Parameters
    ----------
    config:
        Ingest configuration.
    connection:
        Optional pre-built IMAP connection object for dependency injection in
        tests.  When ``None``, the real ``imap-tools`` MailBox is used.
        The connection must support the ``imap-tools`` MailBox API:
        ``.folder.set(name)``, ``.uid_validity``, ``.fetch(criteria, mark_seen=False)``.
        Each fetched message must have a ``.uid`` attribute and an ``.obj``
        attribute (email.message.Message) with ``.as_bytes()`` method.

    Returns
    -------
    pathlib.Path
        The Maildir root (parent of ``cur/``, ``new/``, ``tmp/``).

    Raises
    ------
    ImapCredentialError
        If credentials are missing (when building config via ``from_env``).
    ImportError
        If ``imap-tools`` is not installed and ``connection`` is ``None``.
    """
    if connection is None:
        # Real IMAP connection via imap-tools
        try:
            from imap_tools import MailBox, AND, A
        except ImportError as exc:
            raise ImportError(
                "imap-tools is required for IMAP ingestion. "
                "Install it: pip install imap-tools"
            ) from exc

        # Build search criteria
        if config.since:
            # imap-tools uses date objects for date criteria
            import datetime

            since_date = datetime.date.fromisoformat(config.since)
            criteria = AND(date_gte=since_date)
        else:
            criteria = "ALL"

        with MailBox(config.server, port=config.port, ssl_context=None).login(
            config.user, config.password
        ) as mb:
            mb.folder.set(config.folder)
            uidvalidity = mb.folder.status(config.folder).get("UIDVALIDITY", 0)

            messages: list[ImapMessage] = []
            # READ-ONLY fetch: mark_seen=False, no mutations
            for msg in mb.fetch(criteria, mark_seen=False, bulk=True):
                messages.append(ImapMessage(uid=msg.uid, rfc822=msg.obj.as_bytes()))

        return materialize_maildir(messages, uidvalidity=uidvalidity, config=config)

    else:
        # Injected connection (tests / CI)
        # The caller controls folder selection and uid_validity
        uidvalidity = connection.uid_validity

        messages_out: list[ImapMessage] = []
        for msg in connection.fetch("ALL", mark_seen=False):
            messages_out.append(ImapMessage(uid=msg.uid, rfc822=msg.obj.as_bytes()))

        return materialize_maildir(messages_out, uidvalidity=uidvalidity, config=config)


# ── argument parser ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maildir_report.imap_ingest",
        description=(
            "Fetch messages from an IMAP server (IMAPS/TLS only) and materialise\n"
            "them as a local Maildir for use with the maildir_report pipeline.\n\n"
            "Credentials are read ONLY from environment variables:\n"
            "  IMAP_SERVER  — IMAP server hostname\n"
            "  IMAP_USER    — IMAP login username\n"
            "  IMAP_PASS    — IMAP password or app password\n\n"
            "Output layout:\n"
            "  <data-dir>/imap/<mailbox>/INBOX/Maildir/cur/{uidvalidity}.{uid}.eml\n\n"
            "The operation is READ-ONLY: no server-side mutations are performed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "mailbox",
        metavar="MAILBOX",
        help="Mailbox name (used as output directory label).",
    )
    parser.add_argument(
        "data_dir",
        metavar="DATA_DIR",
        help="Root data directory for output Maildir.",
    )
    parser.add_argument(
        "--folder",
        default="INBOX",
        metavar="FOLDER",
        help="IMAP folder to fetch (default: INBOX).",
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Fetch only messages on or after this date (optional).",
    )
    return parser


# ── entrypoint ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: fetch IMAP messages and materialise Maildir.

    Returns 0 on success, 1 on any error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = ImapIngestConfig.from_env(
            mailbox_name=args.mailbox,
            data_dir=args.data_dir,
            folder=args.folder,
            since=args.since,
        )
    except ImapCredentialError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        maildir_root = run_imap_ingest(config=config)
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"==> IMAP ingest complete. Maildir: {maildir_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

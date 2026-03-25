"""
test_sha256.py — Tests for SHA-256 content hashing (Task 5).

Covers:
  - Equal payloads produce equal digest.
  - Unequal payloads produce different digests.
  - Deterministic: same input hashed multiple times yields same digest.
  - Empty payload behavior (hash of zero bytes is defined and stable).
  - None treated identically to empty bytes.
  - Type error on non-bytes input.
  - Parser wiring: content_hash on parsed parts uses SHA-256 (64-char hex).
"""

from __future__ import annotations

import hashlib

import pytest

from maildir_report.hash import sha256_hex


# ── basic contract ────────────────────────────────────────────────────────────


def test_equal_payloads_produce_equal_digest() -> None:
    """Identical payload bytes must yield the same digest."""
    payload = b"hello world"
    assert sha256_hex(payload) == sha256_hex(payload)


def test_equal_payloads_across_separate_calls() -> None:
    """Two independent calls with equal bytes must agree."""
    a = sha256_hex(b"the quick brown fox")
    b = sha256_hex(b"the quick brown fox")
    assert a == b


def test_unequal_payloads_produce_different_digests() -> None:
    """Different payload bytes must not collide."""
    assert sha256_hex(b"foo") != sha256_hex(b"bar")


def test_single_byte_difference_changes_digest() -> None:
    """Even a one-byte difference must produce a different digest."""
    assert sha256_hex(b"aaaa") != sha256_hex(b"aaab")


# ── determinism ───────────────────────────────────────────────────────────────


def test_deterministic_repeated_hashing() -> None:
    """The same input hashed 100 times always yields the same result."""
    payload = b"repeat me"
    results = {sha256_hex(payload) for _ in range(100)}
    assert len(results) == 1, "Non-deterministic: got multiple distinct digests"


def test_deterministic_empty_repeated() -> None:
    """Empty bytes hashed repeatedly must stay constant."""
    results = {sha256_hex(b"") for _ in range(50)}
    assert len(results) == 1


# ── empty payload ─────────────────────────────────────────────────────────────

# SHA-256 of empty bytes is the well-known constant.
_SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_empty_bytes_produces_known_digest() -> None:
    """Hash of b'' must equal the well-known SHA-256 empty-string constant."""
    assert sha256_hex(b"") == _SHA256_EMPTY


def test_none_treated_as_empty_bytes() -> None:
    """None payload must produce the same digest as empty bytes."""
    assert sha256_hex(None) == sha256_hex(b"")
    assert sha256_hex(None) == _SHA256_EMPTY


def test_bytearray_accepted() -> None:
    """bytearray is a valid input type (same digest as equivalent bytes)."""
    assert sha256_hex(bytearray(b"data")) == sha256_hex(b"data")


# ── output format ─────────────────────────────────────────────────────────────


def test_output_is_64_char_lowercase_hex() -> None:
    """Digest must be exactly 64 lowercase hex characters."""
    digest = sha256_hex(b"test payload")
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_output_matches_stdlib_reference() -> None:
    """sha256_hex must agree with hashlib.sha256 reference implementation."""
    payload = b"reference check"
    expected = hashlib.sha256(payload).hexdigest()
    assert sha256_hex(payload) == expected


# ── type safety ───────────────────────────────────────────────────────────────


def test_type_error_on_string_input() -> None:
    """Passing a str (not bytes) must raise TypeError."""
    with pytest.raises(TypeError):
        sha256_hex("not bytes")  # type: ignore[arg-type]


def test_type_error_on_int_input() -> None:
    """Passing an int must raise TypeError."""
    with pytest.raises(TypeError):
        sha256_hex(42)  # type: ignore[arg-type]


# ── parser integration ────────────────────────────────────────────────────────


def test_parser_content_hash_uses_sha256(tmp_path) -> None:
    """parse_email_file must set content_hash as a 64-char hex SHA-256 digest."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from maildir_report.parser import parse_email_file

    # Build a multipart message with one named binary attachment.
    raw_payload = b"attachment content for hashing test"
    expected_hash = hashlib.sha256(raw_payload).hexdigest()

    msg = MIMEMultipart()
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Hash test"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = "<hash-test@example.com>"
    msg.attach(MIMEText("body", "plain"))
    attachment = MIMEApplication(raw_payload, _subtype="octet-stream")
    attachment.add_header("Content-Disposition", "attachment", filename="file.bin")
    msg.attach(attachment)

    mail_file = tmp_path / "cur" / "1.msg"
    mail_file.parent.mkdir(parents=True)
    mail_file.write_bytes(msg.as_bytes())

    record = parse_email_file(str(mail_file), folder="INBOX")
    parts = record["parts"]
    assert parts, "Expected at least one part in the parsed record"

    hashes = [p["content_hash"] for p in parts]
    assert expected_hash in hashes, (
        f"Expected SHA-256 hash {expected_hash!r} in part hashes {hashes!r}"
    )
    # All hashes must be 64-char hex (SHA-256, not MD5 which is 32 chars).
    for h in hashes:
        assert len(h) == 64, f"Part hash {h!r} is not 64 chars — not SHA-256"
        assert all(c in "0123456789abcdef" for c in h)

def test_parser_zero_byte_part_hash_is_sha256_empty(tmp_path) -> None:
    """A zero-byte attachment must get the SHA-256 hash of empty bytes."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from maildir_report.parser import parse_email_file

    # Build a multipart message with a zero-byte named binary attachment.
    msg = MIMEMultipart()
    msg["From"] = "a@b.com"
    msg["To"] = "b@c.com"
    msg["Subject"] = "Zero byte"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = "<zero-byte@example.com>"
    msg.attach(MIMEText("body", "plain"))
    # Zero-byte attachment — payload is empty.
    attachment = MIMEApplication(b"", _subtype="octet-stream")
    attachment.add_header("Content-Disposition", "attachment", filename="empty.bin")
    msg.attach(attachment)

    mail_file = tmp_path / "cur" / "2.msg"
    mail_file.parent.mkdir(parents=True)
    mail_file.write_bytes(msg.as_bytes())

    record = parse_email_file(str(mail_file), folder="INBOX")
    parts = record["parts"]
    assert parts, "Expected at least one part"

    hashes = [p["content_hash"] for p in parts]
    assert _SHA256_EMPTY in hashes, (
        f"Expected empty-hash {_SHA256_EMPTY!r} in {hashes!r}"
    )

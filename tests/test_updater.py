import gzip
import json
import hashlib
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from mrija_client.updater import fetch_manifest, verify_sha256, decompress_gz


def test_verify_sha256_match(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello")
    expected = hashlib.sha256(b"hello").hexdigest()
    assert verify_sha256(f, expected) is True


def test_verify_sha256_mismatch(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello")
    assert verify_sha256(f, "deadbeef") is False


def test_decompress_gz(tmp_path):
    gz = tmp_path / "test.sqlite.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(b"SQLite data")
    result = decompress_gz(gz)
    assert result == tmp_path / "test.sqlite"
    assert result.read_bytes() == b"SQLite data"
    assert not gz.exists()


def test_fetch_manifest_parses_json():
    manifest = {
        "version": "20260613T000000Z",
        "sha256": "abc",
        "url": "/updates/f.gz",
        "filename": "f.gz",
    }
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(manifest).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_response):
        result = fetch_manifest("http://example.com/updates/manifest.json")
    assert result["version"] == "20260613T000000Z"

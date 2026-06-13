from __future__ import annotations
import gzip
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Callable

UPDATE_SERVER = "http://104.248.242.243"
MANIFEST_PATH = "/updates/manifest.json"


def fetch_manifest(url: str | None = None) -> dict:
    target = url or (UPDATE_SERVER + MANIFEST_PATH)
    with urllib.request.urlopen(target, timeout=10) as r:
        return json.loads(r.read())


def verify_sha256(path: Path, expected: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest() == expected


def decompress_gz(gz_path: Path) -> Path:
    out_path = gz_path.with_suffix("")
    with gzip.open(gz_path, "rb") as gz_in, open(out_path, "wb") as f_out:
        while chunk := gz_in.read(65536):
            f_out.write(chunk)
    gz_path.unlink()
    return out_path


def download_archive(
    url: str,
    dest: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    with urllib.request.urlopen(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            while chunk := r.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and total:
                    on_progress(downloaded, total)


def run_update(state: "AppState", dest_dir: Path) -> None:  # type: ignore[name-defined]
    from mrija_client.state import ClientState
    from mrija_client.db import MailDB

    state.state = ClientState.UPDATING
    state.update_progress = 0
    state.update_status = "Fetching manifest…"

    try:
        manifest = fetch_manifest()
        url = UPDATE_SERVER + manifest["url"]
        gz_dest = dest_dir / manifest["filename"]
        dest_dir.mkdir(parents=True, exist_ok=True)

        state.update_status = "Downloading archive…"

        def _progress(done: int, total: int) -> None:
            state.update_progress = int(done / total * 90)

        download_archive(url, gz_dest, _progress)

        state.update_status = "Verifying checksum…"
        if not verify_sha256(gz_dest, manifest["sha256"]):
            raise ValueError("SHA256 mismatch — download corrupted")

        state.update_status = "Decompressing…"
        state.update_progress = 92
        sqlite_path = decompress_gz(gz_dest)

        state.update_status = "Applying update…"
        state.update_progress = 97
        if state.db:
            state.db.close()
        state.db = MailDB(sqlite_path)
        state.db_path = sqlite_path
        state.version = manifest.get("version", "")

        state.update_progress = 100
        state.update_status = "Done"
        state.state = ClientState.RUNNING

    except Exception as exc:
        state.state = ClientState.ERROR
        state.error_message = str(exc)
        state.update_status = f"Error: {exc}"
        raise

import subprocess
import time
import urllib.request
import pytest
import os
import signal
import sqlite3
import tempfile
import shutil

@pytest.fixture(scope="module")
def test_env():
    # Setup temporary directory for data
    tmp_dir = tempfile.mkdtemp()
    data_dir = os.path.join(tmp_dir, "data")
    os.makedirs(os.path.join(data_dir, "mailboxes/test_mailbox/attachments"))
    
    # Create dummy attachment file
    sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" # empty file
    dummy_file_path = os.path.join(data_dir, "mailboxes/test_mailbox/attachments", f"{sha256}_0.txt")
    with open(dummy_file_path, "w") as f:
        f.write("")

    outside_sha256 = "f" * 64
    outside_dir = os.path.join(data_dir, "mailboxes/test_mailbox/attachments_evil")
    os.makedirs(outside_dir)
    outside_file = os.path.join(outside_dir, "outside.txt")
    with open(outside_file, "w") as f:
        f.write("outside")
    symlink_path = os.path.join(data_dir, "mailboxes/test_mailbox/attachments", f"{outside_sha256}_0.txt")
    os.symlink(outside_file, symlink_path)

    # Setup SQLite DB
    db_path = os.path.join(tmp_dir, "test.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE archive_attachments (
            mailbox           VARCHAR(255) NOT NULL,
            email_stable_id   CHAR(64)     NOT NULL,
            stored_path       TEXT         NOT NULL,
            sha256            CHAR(64)     NOT NULL,
            size              BIGINT       NOT NULL DEFAULT 0,
            mime              VARCHAR(255) NOT NULL DEFAULT '',
            original_filename TEXT         NOT NULL DEFAULT '',
            imported_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id, sha256)
        )
    """)
    conn.execute("""
        CREATE TABLE vt_cache (
            sha256     CHAR(64) PRIMARY KEY,
            status     VARCHAR(32) NOT NULL,
            scan_id    TEXT NOT NULL DEFAULT '',
            positives  INTEGER NOT NULL DEFAULT 0,
            scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT INTO archive_attachments (mailbox, email_stable_id, stored_path, sha256, mime, original_filename) VALUES (?, ?, ?, ?, ?, ?)",
        ("test_mailbox", "email1", f"test_mailbox/attachments/{sha256}_0.txt", sha256, "image/png", "test.png")
    )
    conn.execute(
        "INSERT INTO archive_attachments (mailbox, email_stable_id, stored_path, sha256, mime, original_filename) VALUES (?, ?, ?, ?, ?, ?)",
        ("test_mailbox", "email2", f"test_mailbox/attachments/{outside_sha256}_0.txt", outside_sha256, "text/plain", "outside.txt")
    )
    conn.commit()
    conn.close()

    # Create local.php (Auth disabled for testing)
    config_path = "web/config/local.php"
    with open(config_path, "w") as f:
        f.write(f"<?php return ['auth' => ['enabled' => false], 'session' => [], 'data_dir' => '{data_dir}', 'db' => ['engine' => 'sqlite', 'path' => '{db_path}', 'user' => '', 'password' => '']];")

    # Start PHP server with display_errors=0 to ensure 500 on Fatal Error
    proc = subprocess.Popen(
        ["php", "-d", "display_errors=0", "-S", "localhost:8082", "-t", "web/public"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    time.sleep(1)
    
    yield "http://localhost:8082", sha256, db_path
    
    # Cleanup
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    os.remove(config_path)
    shutil.rmtree(tmp_dir)

def test_download_inline_flag_respected(test_env):
    base_url, sha256, _db_path = test_env
    url = f"{base_url}/download.php?mailbox=test_mailbox&sha256={sha256}&inline=1"
    
    response = urllib.request.urlopen(url)
    assert response.getcode() == 200
    cd = response.headers.get("Content-Disposition", "")
    assert "inline" in cd, f"Expected inline in CD header, got '{cd}'."
    assert "test.png" in cd

def test_download_attachment_by_default(test_env):
    base_url, sha256, _db_path = test_env
    url = f"{base_url}/download.php?mailbox=test_mailbox&sha256={sha256}"
    
    response = urllib.request.urlopen(url)
    assert response.getcode() == 200
    cd = response.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert "test.png" in cd

def test_download_bypass_vt(test_env):
    base_url, sha256, db_path = test_env
    config_path = "web/config/local.php"
    with open(config_path, "r") as f:
        config_content = f.read()
    
    # Temporarily add vt_api_key to local.php
    new_config = config_content.replace("'db' => [", "'vt_api_key' => 'dummy', 'db' => [")
    with open(config_path, "w") as f:
        f.write(new_config)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO vt_cache (sha256, status, scan_id, positives) VALUES (?, ?, ?, ?)",
        (sha256, "infected", "", 3),
    )
    conn.commit()
    conn.close()
        
    try:
        # Without bypass_vt, the cached infected verdict blocks the download.
        url = f"{base_url}/download.php?mailbox=test_mailbox&sha256={sha256}"
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(url)
        assert excinfo.value.code == 403
            
        # With bypass_vt, it should succeed
        url = f"{base_url}/download.php?mailbox=test_mailbox&sha256={sha256}&bypass_vt=1"
        response = urllib.request.urlopen(url)
        assert response.getcode() == 200
        assert "test.png" in response.headers.get("Content-Disposition", "")
    finally:
        # Restore config
        with open(config_path, "w") as f:
            f.write(config_content)

def test_download_rejects_symlink_outside_attachment_dir(test_env):
    base_url, _sha256, _db_path = test_env
    outside_sha256 = "f" * 64
    url = f"{base_url}/download.php?mailbox=test_mailbox&sha256={outside_sha256}"

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(url)
    assert excinfo.value.code == 403

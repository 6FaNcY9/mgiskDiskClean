import json
import os
import signal
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def _post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req)


def test_review_decision_api_saves_and_updates_decision():
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "review.sqlite")
    stable_id = "a" * 64

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE archive_emails (
            mailbox TEXT NOT NULL,
            stable_id TEXT NOT NULL,
            PRIMARY KEY (mailbox, stable_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE review_decisions (
            mailbox TEXT NOT NULL,
            email_stable_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            reviewer_role TEXT NOT NULL DEFAULT '',
            reviewer_name TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, email_stable_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO archive_emails (mailbox, stable_id) VALUES (?, ?)",
        ("testbox", stable_id),
    )
    conn.commit()
    conn.close()

    config_path = "web/config/local.php"
    original_config = None
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            original_config = f.read()
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(
            "<?php return ['auth' => ['enabled' => false], 'session' => [], "
            f"'db' => ['engine' => 'sqlite', 'path' => '{db_path}', 'user' => '', 'password' => '']];"
        )

    proc = subprocess.Popen(
        ["php", "-d", "display_errors=0", "-S", "localhost:8084", "-t", "web/public"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    time.sleep(1)

    try:
        response = _post_json(
            "http://localhost:8084/api/review-decision.php",
            {
                "mailbox": "testbox",
                "email_stable_id": stable_id,
                "decision": "keep",
                "notes": "Looks safe.",
            },
        )
        assert response.getcode() == 200
        assert json.loads(response.read().decode("utf-8"))["ok"] is True

        response = _post_json(
            "http://localhost:8084/api/review-decision.php",
            {
                "mailbox": "testbox",
                "email_stable_id": stable_id,
                "decision": "delete",
                "notes": "No longer needed.",
            },
        )
        assert response.getcode() == 200

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT decision, notes FROM review_decisions WHERE mailbox = ? AND email_stable_id = ?",
            ("testbox", stable_id),
        ).fetchone()
        conn.close()
        assert row == ("delete", "No longer needed.")

        try:
            _post_json(
                "http://localhost:8084/api/review-decision.php",
                {
                    "mailbox": "testbox",
                    "email_stable_id": stable_id,
                    "decision": "archive",
                    "notes": "",
                },
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("invalid decision should return HTTP 400")
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        if original_config is None:
            os.remove(config_path)
        else:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(original_config)
        shutil.rmtree(tmp_dir)

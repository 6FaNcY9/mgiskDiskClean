import subprocess
import time
import urllib.request
import pytest
import os
import signal

@pytest.fixture(scope="module")
def php_server():
    # Ensure a config exists for the server to start
    config_path = "web/config/local.php"
    config_created = False
    if not os.path.exists(config_path):
        with open(config_path, "w") as f:
            f.write("<?php return ['session' => [], 'db' => ['host'=>'127.0.0.1', 'dbname'=>'test', 'user'=>'test', 'password'=>'test', 'charset'=>'utf8mb4']];")
        config_created = True

    # Start PHP built-in server
    proc = subprocess.Popen(
        ["php", "-S", "localhost:8081", "-t", "web/public"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    
    # Wait for server to start
    time.sleep(1)
    
    yield "http://localhost:8081"
    
    # Cleanup
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    if config_created:
        os.remove(config_path)

def test_php_server_responding(php_server):
    """Verify that the PHP server responds to requests."""
    response = urllib.request.urlopen(php_server)
    assert response.getcode() == 200
    content = response.read().decode('utf-8')
    # index.php should show 'Config not found' or redirect if config is valid but DB fails
    # With our dummy config, it might try to connect to DB and show 'Database starting up'
    assert "Database starting up" in content or "Config not found" in content or "Login" in content or "Anmelden" in content

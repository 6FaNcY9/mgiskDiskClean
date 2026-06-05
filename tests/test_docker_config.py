import re
import pytest
import os

def test_docker_compose_best_practices():
    with open("docker-compose.yml", "r") as f:
        content = f.read()
    
    # 1. Web service should have a healthcheck
    # We look for 'web:' followed by 'healthcheck:' before the next service
    web_section = re.search(r'web:.*?(?=\n\S|$)', content, re.DOTALL)
    assert web_section, "Could not find web service in docker-compose.yml"
    assert "healthcheck:" in web_section.group(0), "Web service is missing a healthcheck"
    
    # 2. Ports should be isolated to 127.0.0.1
    assert "127.0.0.1:${MRIJA_WEB_PORT:-8080}:8080" in content

def test_env_example_completeness():
    with open(".env.example", "r") as f:
        content = f.read()
    
    required_vars = ["VT_API_KEY", "MRIJA_WEB_PORT", "MRIJA_DB_PASSWORD"]
    for var in required_vars:
        assert var in content, f"{var} is missing from .env.example"

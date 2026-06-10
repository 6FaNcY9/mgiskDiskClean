# Dockerfile
FROM python:3.11-slim-bookworm

# System packages: PHP 8.2 (bookworm default), rsync, MariaDB client, sqlite3
RUN apt-get update && apt-get install -y --no-install-recommends \
        php-cli \
        php-pdo \
        php-mysql \
        php-mbstring \
        php-sqlite3 \
        rsync \
        default-mysql-client \
        sqlite3 \
        curl \
        jq \
        zstd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir "pytest>=8.0"

# Copy source
COPY src/ ./src/
COPY tests/ ./tests/
COPY web/ ./web/

# Copy docker DB config as local.php (non-secret; real creds live in compose env)
COPY web/config/local.php.docker /app/web/config/local.php

ENV PYTHONPATH=/app/src

CMD ["bash"]

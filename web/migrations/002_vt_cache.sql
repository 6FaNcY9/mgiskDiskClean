-- web/migrations/002_vt_cache.sql
-- VirusTotal scan result cache, keyed by file SHA-256.
-- Applied by: docker compose run --rm app php web/src/cli/migrate.php

CREATE TABLE IF NOT EXISTS vt_cache (
    sha256      CHAR(64)     NOT NULL,
    status      ENUM('pending','clean','infected','error') NOT NULL DEFAULT 'pending',
    scan_id     VARCHAR(64)  NOT NULL DEFAULT '',
    positives   TINYINT      NOT NULL DEFAULT 0,
    total       SMALLINT     NOT NULL DEFAULT 0,
    scanned_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                             ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

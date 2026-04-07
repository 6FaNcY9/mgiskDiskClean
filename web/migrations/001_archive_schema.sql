-- web/migrations/001_archive_schema.sql
-- Archive tables for the mailbox search database.
-- Replaces the old review workflow tables.

CREATE TABLE IF NOT EXISTS archive_emails (
  mailbox          VARCHAR(255) NOT NULL,
  stable_id        CHAR(64)     NOT NULL,
  filepath         TEXT         NOT NULL,
  folder           VARCHAR(255) NOT NULL DEFAULT '',
  date             VARCHAR(64)  NOT NULL DEFAULT '',
  from_addr        VARCHAR(255) NOT NULL DEFAULT '',
  to_addrs         TEXT         NOT NULL DEFAULT '',
  cc_addrs         TEXT         NOT NULL DEFAULT '',
  subject          TEXT         NOT NULL DEFAULT '',
  body_text        LONGTEXT     NOT NULL DEFAULT '',
  total_size_bytes BIGINT       NOT NULL DEFAULT 0,
  imported_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (mailbox, stable_id),
  KEY idx_archive_emails_date (mailbox, date),
  FULLTEXT KEY ftx_archive_emails (subject, from_addr, to_addrs, cc_addrs, body_text)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS archive_attachments (
  mailbox           VARCHAR(255) NOT NULL,
  email_stable_id   CHAR(64)     NOT NULL,
  sha256            CHAR(64)     NOT NULL,
  size              BIGINT       NOT NULL DEFAULT 0,
  mime              VARCHAR(255) NOT NULL DEFAULT '',
  original_filename TEXT         NOT NULL DEFAULT '',
  stored_path       TEXT         NOT NULL DEFAULT '',
  imported_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (mailbox, email_stable_id, sha256),
  KEY idx_archive_attachments_email (mailbox, email_stable_id),
  KEY idx_archive_attachments_sha256 (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

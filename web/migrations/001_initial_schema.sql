-- web/migrations/001_initial_schema.sql
-- Initial schema for the mailbox review app.
-- Keyed by report_id = manifest.pdf_sha256 (per Task 3 spec).

-- Reports table: one row per imported pipeline run
CREATE TABLE IF NOT EXISTS reports (
    report_id          VARCHAR(255)  NOT NULL,
    mailbox            VARCHAR(255)  NOT NULL,
    generated_at       VARCHAR(64)   NOT NULL,
    pdf_path           TEXT          NOT NULL DEFAULT '',
    manifest_path      TEXT          NOT NULL DEFAULT '',
    decisions_seed_path TEXT         NOT NULL DEFAULT '',
    imported_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (report_id),
    KEY idx_reports_mailbox (mailbox)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Emails table: one row per email per report (stable_id local to a report run)
CREATE TABLE IF NOT EXISTS emails (
    report_id         VARCHAR(255)  NOT NULL,
    stable_id         VARCHAR(255)  NOT NULL,
    folder            VARCHAR(255)  NOT NULL DEFAULT '',
    date              VARCHAR(64)   NOT NULL DEFAULT '',
    sender            VARCHAR(255)  NOT NULL DEFAULT '',
    subject           TEXT          NOT NULL DEFAULT '',
    total_size_bytes  BIGINT        NOT NULL DEFAULT 0,
    is_duplicate      TINYINT(1)    NOT NULL DEFAULT 0,
    dup_group_id      VARCHAR(255)  NOT NULL DEFAULT '',
    dup_rank          INT           NOT NULL DEFAULT -1,
    PRIMARY KEY (report_id, stable_id),
    KEY idx_emails_decision_lookup (report_id, is_duplicate),
    KEY idx_emails_dup_group (report_id, dup_group_id),
    KEY idx_emails_subject (report_id, subject(64)),
    KEY idx_emails_sender (report_id, sender(128)),
    CONSTRAINT fk_emails_report FOREIGN KEY (report_id) REFERENCES reports (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Decisions table: coworker review state (kept separate from immutable emails)
CREATE TABLE IF NOT EXISTS decisions (
    report_id   VARCHAR(255)  NOT NULL,
    stable_id   VARCHAR(255)  NOT NULL,
    decision    ENUM('keep','delete','unsure','') NOT NULL DEFAULT '',
    note        TEXT          NOT NULL DEFAULT '',
    updated_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by  VARCHAR(255)  NOT NULL DEFAULT '',
    PRIMARY KEY (report_id, stable_id),
    KEY idx_decisions_decision (report_id, decision),
    KEY idx_decisions_updated_by (report_id, updated_by),
    CONSTRAINT fk_decisions_report FOREIGN KEY (report_id) REFERENCES reports (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

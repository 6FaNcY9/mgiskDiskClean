-- 004_review_decisions.sql
-- Persist per-email review decisions from the production review UI.

CREATE TABLE IF NOT EXISTS review_decisions (
    mailbox          VARCHAR(255) NOT NULL,
    email_stable_id  CHAR(64)     NOT NULL,
    decision         ENUM('keep', 'delete', 'unsure') NOT NULL,
    notes            TEXT         NOT NULL,
    reviewer_role    VARCHAR(32)  NOT NULL DEFAULT '',
    reviewer_name    VARCHAR(255) NOT NULL DEFAULT '',
    decided_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (mailbox, email_stable_id),
    KEY idx_decision (decision),
    KEY idx_decided_at (decided_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
